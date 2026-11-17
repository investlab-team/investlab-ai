import hashlib
import time
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import (
    JSON,
    URL,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    case,
    create_engine,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, defer, mapped_column

from investlab_ai.config import DatabaseSettings, Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.parser import ParsedChunk
from investlab_ai.object_storage import EvidenceObjects
from investlab_ai.private_cache import PrivateCache
from investlab_ai.schemas import Answer, Company, Disclosure, Evidence, PreparedDocument

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
PARSER_VERSION = "dart-sections-v1"


class Base(DeclarativeBase):
    pass


class CompanyRow(Base):
    __tablename__ = "rag_companies"
    corp_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON_TYPE)


class DocumentRow(Base):
    __tablename__ = "rag_documents"
    rcept_no: Mapped[str] = mapped_column(String(14), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), unique=True)
    disclosure: Mapped[dict] = mapped_column(JSON_TYPE)
    content_hash: Mapped[str] = mapped_column(String(64))
    original_zip: Mapped[bytes | None] = mapped_column(LargeBinary)
    original_object_key: Mapped[str | None] = mapped_column(Text)
    chunks_object_key: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_dimensions: Mapped[int] = mapped_column(Integer)
    parser_version: Mapped[str] = mapped_column(String(40))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    chunk_count: Mapped[int] = mapped_column(Integer)
    warnings: Mapped[list] = mapped_column(JSON_TYPE)


class ChunkRow(Base):
    __tablename__ = "rag_chunks"
    chunk_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rcept_no: Mapped[str] = mapped_column(
        ForeignKey("rag_documents.rcept_no"), index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list] = mapped_column(JSON_TYPE)


class AnalysisRow(Base):
    __tablename__ = "rag_analyses"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    rcept_no: Mapped[str] = mapped_column(ForeignKey("rag_documents.rcept_no"), index=True)
    result: Mapped[dict] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskRow(Base):
    __tablename__ = "rag_chat_tasks"
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict] = mapped_column(JSON_TYPE)


class BulkRow(Base):
    __tablename__ = "rag_bulk_runs"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON_TYPE)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BulkItemRow(Base):
    __tablename__ = "rag_bulk_items"
    run_id: Mapped[str] = mapped_column(ForeignKey("rag_bulk_runs.run_id"), primary_key=True)
    rcept_no: Mapped[str] = mapped_column(String(14), primary_key=True)
    disclosure: Mapped[dict] = mapped_column(JSON_TYPE)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(80))


class AnalysisLeaseRow(Base):
    __tablename__ = "rag_analysis_leases"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def database_url(settings: Settings, *, direct: bool = False) -> URL:
    if settings.local_sqlite_path is not None:
        path = settings.local_sqlite_path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return URL.create("sqlite+pysqlite", database=str(path))
    db = DatabaseSettings()
    secret_url = (db.direct_url or db.connection_url) if direct else db.connection_url
    if secret_url:
        try:
            url = make_url(secret_url.get_secret_value())
        except ArgumentError:
            raise ServiceError("INVALID_DATABASE_URL", 503) from None
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ServiceError("INVALID_DATABASE_URL", 503)
        if direct and url.host and "-pooler." in url.host:
            raise ServiceError("DIRECT_DATABASE_URL_REQUIRED", 503)
        return url.set(drivername="postgresql+psycopg")
    return URL.create(
        "postgresql+psycopg", username=db.username, password=db.password.get_secret_value(),
        host=db.host, port=db.port, database=db.name,
    )


class Repository:
    def __init__(self, settings: Settings):
        self.task_namespace = hashlib.sha256(
            (settings.instance_id or settings.service_token).encode(),
        ).hexdigest()
        url = database_url(settings)
        options = {"connect_args": {"timeout": 60}} if url.get_backend_name() == "sqlite" else {}
        self.engine = create_engine(url, pool_pre_ping=True, **options)
        self.objects = EvidenceObjects(settings.object_bucket) if settings.object_bucket else None
        self.private_tasks = PrivateCache(settings.private_cache_dir / "tasks",
                                          self.task_namespace, max_entries=512)
        self._listed_catalog: tuple[float, tuple[Company, ...]] | None = None

    def initialize_local(self) -> None:
        if self.engine.dialect.name != "sqlite":
            raise ServiceError("USE_ALEMBIC_FOR_POSTGRESQL", 503)
        Base.metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            if connection.exec_driver_sql("PRAGMA journal_mode").scalar() != "wal":
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        self.engine.dispose()

    def save_companies(self, companies: list[Company]) -> None:
        self._listed_catalog = None
        with Session(self.engine) as session, session.begin():
            existing = {row.corp_code: row for row in session.scalars(select(CompanyRow))}
            for company in companies:
                if company.corp_code in existing:
                    existing[company.corp_code].data = company.model_dump(mode="json")
                else:
                    session.add(CompanyRow(
                        corp_code=company.corp_code, data=company.model_dump(mode="json"),
                    ))

    def search_companies(self, query: str, limit: int = 20) -> list[Company]:
        # Filter in PostgreSQL: never download the whole 120k-company catalog per question.
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        name = CompanyRow.data["corp_name"].as_string()
        stock = CompanyRow.data["stock_code"].as_string()
        with Session(self.engine) as session:
            statement = select(CompanyRow).where(
                name.ilike(f"%{escaped}%", escape="\\") | (stock == query)
                | (CompanyRow.corp_code == query),
            ).order_by(case((name == query, 0), else_=1), name).limit(limit)
            return [Company.model_validate(row.data) for row in session.scalars(statement)]

    def has_companies(self) -> bool:
        with Session(self.engine) as session:
            return session.scalar(select(CompanyRow.corp_code).limit(1)) is not None

    def all_companies(self, listed_only: bool = False) -> list[Company]:
        if listed_only and self._listed_catalog and self._listed_catalog[0] > time.monotonic():
            return list(self._listed_catalog[1])
        with Session(self.engine) as session:
            statement = select(CompanyRow)
            if listed_only:
                statement = statement.where(CompanyRow.data["stock_code"].as_string().is_not(None))
            result = [Company.model_validate(row.data) for row in session.scalars(statement)]
        if listed_only:
            self._listed_catalog = (time.monotonic() + 300, tuple(result))
        return result

    def company(self, corp_code: str) -> Company | None:
        with Session(self.engine) as session:
            row = session.get(CompanyRow, corp_code)
            return Company.model_validate(row.data) if row else None

    @staticmethod
    def prepared(row: DocumentRow) -> PreparedDocument:
        return PreparedDocument(
            document_id=row.document_id, disclosure=Disclosure.model_validate(row.disclosure),
            chunk_count=row.chunk_count, embedding_model=row.embedding_model,
            content_hash=row.content_hash, warnings=row.warnings,
        )

    def document(self, receipt: str) -> PreparedDocument | None:
        with Session(self.engine) as session:
            row = session.scalar(select(DocumentRow).options(defer(DocumentRow.original_zip)).where(
                DocumentRow.rcept_no == receipt,
            ))
            return self.prepared(row) if row else None

    def stored_disclosures(self, corp_code: str, start=None, end=None) -> list[Disclosure]:
        with Session(self.engine) as session:
            filed = DocumentRow.disclosure["filed_on"].as_string()
            query = select(DocumentRow.disclosure).where(
                DocumentRow.disclosure["corp_code"].as_string() == corp_code,
            )
            if start:
                query = query.where(filed >= start.isoformat())
            if end:
                query = query.where(filed <= end.isoformat())
            query = query.order_by(filed.desc(), DocumentRow.rcept_no.desc())
            return [Disclosure.model_validate(data) for data in session.scalars(query)]

    def save_document(
        self, disclosure: Disclosure, original: bytes, content_hash: str,
        model: str, chunks: list[ParsedChunk], vectors: list[list[float]], warnings: list[str],
    ) -> PreparedDocument:
        original_key = chunks_key = None
        records = [{"chunk_id": chunk.chunk_id, "ordinal": index, "section": chunk.section,
                    "text": chunk.text, "token_count": chunk.token_count, "embedding": vector}
                   for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))]
        if self.objects:
            original_key = self.objects.put(disclosure.rcept_no, "original.zip", original)
            chunks_key = self.objects.put(disclosure.rcept_no, "chunks.json.gz",
                                          self.objects.encode_chunks(disclosure.rcept_no, records))
        try:
            with Session(self.engine) as session, session.begin():
                row = DocumentRow(
                    rcept_no=disclosure.rcept_no,
                    document_id=str(uuid5(NAMESPACE_URL, f"opendart:{disclosure.rcept_no}")),
                    disclosure=disclosure.model_dump(mode="json"), content_hash=content_hash,
                    original_zip=None if self.objects else original, embedding_model=model,
                    original_object_key=original_key, chunks_object_key=chunks_key,
                    embedding_dimensions=len(vectors[0]) if vectors else 0,
                    parser_version=PARSER_VERSION, collected_at=datetime.now(UTC),
                    chunk_count=len(chunks), warnings=warnings,
                )
                session.add(row)
                session.flush()
                if not self.objects:
                    session.add_all([ChunkRow(rcept_no=disclosure.rcept_no, **record)
                                     for record in records])
                result = self.prepared(row)
            return result
        except IntegrityError as exc:
            # An atomic insert prevents partial documents or silent replacement of past evidence.
            cached = self.document(disclosure.rcept_no)
            if cached and cached.content_hash == content_hash and cached.embedding_model == model:
                return cached
            raise ServiceError("DOCUMENT_VERSION_CONFLICT", 409) from exc

    def chunks(self, receipt: str) -> list[tuple[Evidence, list[float], int]]:
        document = self.document(receipt)
        if document is None:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        with Session(self.engine) as session:
            row = session.get(DocumentRow, receipt)
            if row.chunks_object_key:
                if not self.objects:
                    raise ServiceError("EVIDENCE_STORAGE_NOT_CONFIGURED", 503)
                records = self.objects.chunks(row.chunks_object_key, receipt, row.chunk_count)
                return [(Evidence(chunk_id=item["chunk_id"], section=item["section"],
                                  text=item["text"], source_url=document.disclosure.source_url),
                         item["embedding"], item["token_count"]) for item in records]
            rows = session.scalars(select(ChunkRow).where(
                ChunkRow.rcept_no == receipt,
            ).order_by(ChunkRow.ordinal))
            return [(
                Evidence(chunk_id=row.chunk_id, section=row.section, text=row.text,
                         source_url=document.disclosure.source_url),
                row.embedding, row.token_count,
            ) for row in rows]

    def index_document(self, receipt: str, model: str, vectors: list[list[float]]) -> None:
        with Session(self.engine) as session, session.begin():
            document = session.get(DocumentRow, receipt)
            if document and document.chunks_object_key:
                if not self.objects:
                    raise ServiceError("EVIDENCE_STORAGE_NOT_CONFIGURED", 503)
                records = self.objects.chunks(document.chunks_object_key, receipt,
                                              document.chunk_count)
                if len(records) != len(vectors) or not vectors:
                    raise ServiceError("EMBEDDING_INVALID_RESPONSE")
                for record, vector in zip(records, vectors, strict=True):
                    record["embedding"] = vector
                document.chunks_object_key = self.objects.put(
                    receipt, "chunks.json.gz", self.objects.encode_chunks(receipt, records))
                document.embedding_model = model
                document.embedding_dimensions = len(vectors[0])
                return
            rows = list(session.scalars(select(ChunkRow).where(
                ChunkRow.rcept_no == receipt,
            ).order_by(ChunkRow.ordinal)))
            if document is None or len(rows) != len(vectors):
                raise ServiceError("EMBEDDING_INVALID_RESPONSE")
            for row, vector in zip(rows, vectors, strict=True):
                row.embedding = vector
            document.embedding_model = model
            document.embedding_dimensions = len(vectors[0])

    def original(self, receipt: str) -> bytes:
        with Session(self.engine) as session:
            row = session.get(DocumentRow, receipt)
            if row is None:
                raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
            if row.original_object_key:
                if not self.objects:
                    raise ServiceError("EVIDENCE_STORAGE_NOT_CONFIGURED", 503)
                return self.objects.get(row.original_object_key)
            return row.original_zip

    def populate_chunks(self, receipt: str, chunks: list[ParsedChunk]) -> None:
        try:
            with Session(self.engine) as session, session.begin():
                document = session.get(DocumentRow, receipt)
                if document is None:
                    raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
                if document.chunk_count:
                    return
                records = [{"chunk_id": chunk.chunk_id, "ordinal": index,
                            "section": chunk.section, "text": chunk.text,
                            "token_count": chunk.token_count, "embedding": []}
                           for index, chunk in enumerate(chunks)]
                if self.objects:
                    document.chunks_object_key = self.objects.put(
                        receipt, "chunks.json.gz", self.objects.encode_chunks(receipt, records))
                else:
                    session.add_all([ChunkRow(rcept_no=receipt, **record) for record in records])
                document.chunk_count = len(chunks)
                document.warnings = [*[warning for warning in document.warnings if "뷰어" in warning],
                                     "복잡한 표와 첨부 자료는 공시 원문을 함께 확인하세요."]
        except IntegrityError:
            document = self.document(receipt)
            if not document or not document.chunk_count:
                raise

    def analysis(self, cache_key: str) -> Answer | None:
        with Session(self.engine) as session:
            row = session.get(AnalysisRow, cache_key)
            return Answer.model_validate(row.result) if row else None

    def basic_analysis_for_report(self, receipt: str) -> Answer | None:
        with Session(self.engine) as session:
            data = session.scalar(select(AnalysisRow.result).where(
                AnalysisRow.rcept_no == receipt,
                AnalysisRow.result["analysis_scope"].as_string() == "basic",
            ).order_by(AnalysisRow.created_at.desc()).limit(1))
            return Answer.model_validate(data) if data else None

    def save_analysis(self, cache_key: str, result: Answer) -> None:
        with Session(self.engine) as session, session.begin():
            if session.get(AnalysisRow, cache_key) is None:
                session.add(AnalysisRow(
                    cache_key=cache_key, rcept_no=result.rcept_no,
                    result=result.model_dump(mode="json"), created_at=datetime.now(UTC),
                ))

    def claim_analysis(self, cache_key: str, owner: str) -> bool:
        now = datetime.now(UTC)
        try:
            with Session(self.engine) as session, session.begin():
                existing = session.get(AnalysisLeaseRow, cache_key)
                if existing:
                    result = session.execute(update(AnalysisLeaseRow).where(
                        AnalysisLeaseRow.cache_key == cache_key,
                        AnalysisLeaseRow.expires_at < now,
                    ).values(owner=owner, expires_at=now + timedelta(minutes=10))
                      .execution_options(synchronize_session=False))
                    return result.rowcount == 1
                session.add(AnalysisLeaseRow(cache_key=cache_key, owner=owner,
                                             expires_at=now + timedelta(minutes=10)))
            return True
        except IntegrityError:
            return False

    def release_analysis(self, cache_key: str, owner: str) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(delete(AnalysisLeaseRow).where(
                AnalysisLeaseRow.cache_key == cache_key, AnalysisLeaseRow.owner == owner,
            ))

    def interrupt_chat_tasks(self) -> None:
        with Session(self.engine) as session, session.begin():
            query = select(TaskRow).where(
                TaskRow.data["_runtime"].as_string() == self.task_namespace,
            )
            for row in session.scalars(query):
                if row.data.get("status") == "running":
                    row.data = {"status": "error", "code": "SERVER_RESTARTED",
                                "_runtime": self.task_namespace,
                                "message": "서버가 재시작됐어요. 같은 질문을 다시 보내 주세요."}

    def put_task(self, task_id: str, owner: str, data: dict) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(TaskRow(task_id=task_id, owner=owner,
                                  data={**data, "_runtime": self.task_namespace}))

    def put_private_task(self, task_id: str, owner: str, data: dict) -> None:
        self.private_tasks.put(task_id, {"owner": owner, "data": data}, 7 * 86400)
        self.put_task(task_id, owner, {"status": "completed", "kind": "private_market",
                                      "message": "이 대화는 생성한 PC에 보관돼요. 로컬 보관기간이 지나면 새로 질문해 주세요."})

    def task(self, task_id: str, owner: str) -> dict | None:
        private = self.private_tasks.get(task_id)
        if private and private.get("owner") == owner:
            return private["data"]
        with Session(self.engine) as session:
            row = session.get(TaskRow, task_id)
            if not row or row.owner != owner:
                return None
            if row.data.get("_runtime", self.task_namespace) != self.task_namespace:
                return None
            return {key: value for key, value in row.data.items() if key != "_runtime"}

    def save_bulk(self, run_id: str, data: dict) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(BulkRow(run_id=run_id, data=data, updated_at=datetime.now(UTC)))

    def bulk(self, run_id: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.get(BulkRow, run_id)
            return row.data if row else None

    def latest_bulk(self) -> dict | None:
        with Session(self.engine) as session:
            row = session.scalar(select(BulkRow).order_by(BulkRow.updated_at.desc()).limit(1))
            return row.data if row else None

    def counts(self) -> dict:
        from sqlalchemy import func
        with Session(self.engine) as session:
            return {"documents": session.scalar(select(func.count()).select_from(DocumentRow)),
                    "analyses": session.scalar(select(func.count()).select_from(AnalysisRow))}

    def basic_analysis_counts(self) -> dict:
        from sqlalchemy import func
        with Session(self.engine) as session:
            reports, companies = session.execute(select(
                func.count(func.distinct(AnalysisRow.rcept_no)),
                func.count(func.distinct(DocumentRow.disclosure["corp_code"].as_string())),
            ).join(DocumentRow, DocumentRow.rcept_no == AnalysisRow.rcept_no).where(
                AnalysisRow.result["analysis_scope"].as_string() == "basic",
            )).one()
            return {"basicReports": reports, "analyzedCompanies": companies}

    def add_bulk_items(self, run_id: str, disclosures: list[Disclosure]) -> None:
        with Session(self.engine) as session, session.begin():
            existing = set(session.scalars(select(BulkItemRow.rcept_no).where(
                BulkItemRow.run_id == run_id,
            )))
            for disclosure in disclosures:
                if disclosure.rcept_no not in existing:
                    session.add(BulkItemRow(
                        run_id=run_id, rcept_no=disclosure.rcept_no,
                        disclosure=disclosure.model_dump(mode="json"), status="pending",
                    ))
                    existing.add(disclosure.rcept_no)

    def bulk_items(self, run_id: str, unfinished_only: bool = True) -> list[Disclosure]:
        with Session(self.engine) as session:
            statement = select(BulkItemRow).where(BulkItemRow.run_id == run_id)
            if unfinished_only:
                statement = statement.where(BulkItemRow.status != "completed")
            return [Disclosure.model_validate(row.disclosure) for row in session.scalars(statement)]

    def bulk_item_status(self, run_id: str, receipt: str, status: str, error: str | None = None):
        with Session(self.engine) as session, session.begin():
            row = session.get(BulkItemRow, (run_id, receipt))
            if row.status == "completed" and status != "completed":
                return
            row.status, row.error_code = status, error

    def claim_bulk_item(self, run_id: str, receipt: str) -> bool:
        with Session(self.engine) as session, session.begin():
            result = session.execute(update(BulkItemRow).where(
                BulkItemRow.run_id == run_id, BulkItemRow.rcept_no == receipt,
                BulkItemRow.status.in_(["pending", "collected", "failed"]),
            ).values(status="running", error_code=None))
            return result.rowcount == 1

    def recover_bulk(self, run_id: str) -> None:
        """Explicit recovery only, after the previous worker process has stopped."""
        with Session(self.engine) as session, session.begin():
            session.execute(update(BulkItemRow).where(
                BulkItemRow.run_id == run_id, BulkItemRow.status == "running",
            ).values(status="collected", error_code=None))

    def bulk_counts(self, run_id: str) -> dict:
        from sqlalchemy import func
        with Session(self.engine) as session:
            rows = session.execute(select(BulkItemRow.status, func.count()).where(
                BulkItemRow.run_id == run_id,
            ).group_by(BulkItemRow.status))
            counts = dict(rows.all())
            return {"total": sum(counts.values()), "completed": counts.get("completed", 0),
                    "failed": counts.get("failed", 0), "running": counts.get("running", 0),
                    "collected": counts.get("collected", 0) + counts.get("completed", 0)}
