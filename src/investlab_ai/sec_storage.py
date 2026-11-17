"""SEC public filings have independent identifiers; never fabricate DART receipt numbers."""
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from investlab_ai.schemas import OverseasCompany, SecFiling
from investlab_ai.storage import JSON_TYPE, Base, Repository


class SecIssuerRow(Base):
    __tablename__ = "rag_sec_issuers"
    cik: Mapped[str] = mapped_column(String(10), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON_TYPE)


class SecDocumentRow(Base):
    __tablename__ = "rag_sec_filings"
    accession: Mapped[str] = mapped_column(String(20), primary_key=True)
    cik: Mapped[str] = mapped_column(ForeignKey("rag_sec_issuers.cik"), index=True)
    data: Mapped[dict] = mapped_column(JSON_TYPE)
    content_hash: Mapped[str] = mapped_column(String(64))
    original_key: Mapped[str] = mapped_column(Text)
    chunks_key: Mapped[str] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(40))
    chunk_count: Mapped[int] = mapped_column(Integer)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecAnalysisRow(Base):
    __tablename__ = "rag_sec_analyses"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    accession: Mapped[str] = mapped_column(ForeignKey("rag_sec_filings.accession"), index=True)
    result: Mapped[dict] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecRunRow(Base):
    __tablename__ = "rag_sec_runs"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON_TYPE)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecItemRow(Base):
    __tablename__ = "rag_sec_items"
    run_id: Mapped[str] = mapped_column(ForeignKey("rag_sec_runs.run_id"), primary_key=True)
    accession: Mapped[str] = mapped_column(String(20), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON_TYPE)
    status: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(80))


class SecRepository:
    def __init__(self, repo: Repository):
        self.repo, self.engine = repo, repo.engine

    def save_issuer(self, issuer: OverseasCompany):
        if not issuer.cik:
            return
        with Session(self.engine) as session, session.begin():
            session.merge(SecIssuerRow(cik=issuer.cik, data=issuer.model_dump(mode="json", by_alias=True)))

    def issuers(self) -> list[OverseasCompany]:
        with Session(self.engine) as session:
            return [OverseasCompany.model_validate(r.data) for r in session.scalars(select(SecIssuerRow))]

    def issuer(self, cik: str) -> OverseasCompany | None:
        with Session(self.engine) as session:
            row = session.get(SecIssuerRow, cik)
            return OverseasCompany.model_validate(row.data) if row else None

    def document(self, accession: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.get(SecDocumentRow, accession)
            return {c.name: getattr(row, c.name) for c in row.__table__.columns} if row else None

    def save_document(self, filing: SecFiling, content_hash: str, original_key: str,
                      chunks_key: str, chunk_count: int, parser_version: str):
        with Session(self.engine) as session, session.begin():
            row = session.get(SecDocumentRow, filing.accession)
            if row:
                if row.content_hash != content_hash:
                    raise RuntimeError("SEC_DOCUMENT_VERSION_CONFLICT")
                return
            session.add(SecDocumentRow(accession=filing.accession, cik=filing.cik,
                                       data=filing.model_dump(mode="json", by_alias=True),
                                       content_hash=content_hash, original_key=original_key,
                                       chunks_key=chunks_key, chunk_count=chunk_count,
                                       parser_version=parser_version, collected_at=datetime.now(UTC)))

    def filings(self, cik: str) -> list[SecFiling]:
        with Session(self.engine) as session:
            rows = list(session.scalars(select(SecDocumentRow.data).where(SecDocumentRow.cik == cik)))
        return sorted((SecFiling.model_validate(r) for r in rows),
                      key=lambda r: (r.filed_on, r.accession), reverse=True)

    def analysis(self, key: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.get(SecAnalysisRow, key)
            return row.result if row else None

    def latest_basic(self, accession: str) -> dict | None:
        with Session(self.engine) as session:
            return session.scalar(select(SecAnalysisRow.result).where(
                SecAnalysisRow.accession == accession,
                SecAnalysisRow.result["analysisScope"].as_string() == "basic",
            ).order_by(SecAnalysisRow.created_at.desc()).limit(1))

    def save_analysis(self, key: str, accession: str, result: dict):
        with Session(self.engine) as session, session.begin():
            if session.get(SecAnalysisRow, key) is None:
                session.add(SecAnalysisRow(cache_key=key, accession=accession,
                                           result=result, created_at=datetime.now(UTC)))

    def save_run(self, run_id: str, data: dict):
        with Session(self.engine) as session, session.begin():
            session.merge(SecRunRow(run_id=run_id, data=data, updated_at=datetime.now(UTC)))

    def run(self, run_id: str | None = None) -> dict | None:
        with Session(self.engine) as session:
            row = (session.get(SecRunRow, run_id) if run_id else
                   session.scalar(select(SecRunRow).order_by(SecRunRow.updated_at.desc()).limit(1)))
            return row.data if row else None

    def add_items(self, run_id: str, filings: list[SecFiling]):
        with Session(self.engine) as session, session.begin():
            known = set(session.scalars(select(SecItemRow.accession).where(SecItemRow.run_id == run_id)))
            session.add_all([SecItemRow(run_id=run_id, accession=f.accession,
                                       data=f.model_dump(mode="json", by_alias=True), status="pending")
                             for f in filings if f.accession not in known])

    def items(self, run_id: str) -> list[tuple[SecFiling, str]]:
        with Session(self.engine) as session:
            return [(SecFiling.model_validate(r.data), r.status) for r in session.scalars(
                select(SecItemRow).where(SecItemRow.run_id == run_id).order_by(SecItemRow.accession))]

    def item_status(self, run_id: str, accession: str, status: str, error: str | None = None):
        with Session(self.engine) as session, session.begin():
            row = session.get(SecItemRow, (run_id, accession))
            row.status, row.error_code = status, error

    def counts(self) -> dict:
        with Session(self.engine) as session:
            return {"companies": session.scalar(select(func.count()).select_from(SecIssuerRow)),
                    "documents": session.scalar(select(func.count()).select_from(SecDocumentRow)),
                    "analyses": session.scalar(select(func.count(func.distinct(SecAnalysisRow.accession)))),
                    "analyzedCompanies": session.scalar(select(func.count(func.distinct(
                        SecDocumentRow.cik))).join(SecAnalysisRow, SecDocumentRow.accession == SecAnalysisRow.accession))}

    def run_counts(self, run_id: str) -> dict:
        with Session(self.engine) as session:
            documents = select(SecDocumentRow.accession).join(
                SecItemRow, SecItemRow.accession == SecDocumentRow.accession,
            ).where(SecItemRow.run_id == run_id).subquery()
            analyzed = select(SecAnalysisRow.accession).where(
                SecAnalysisRow.accession.in_(select(documents.c.accession)),
                SecAnalysisRow.accession.in_(select(SecItemRow.accession).where(
                    SecItemRow.run_id == run_id, SecItemRow.status == "analyzed")),
            ).distinct().subquery()
            return {
                "collected": session.scalar(select(func.count()).select_from(documents)),
                "analyzed": session.scalar(select(func.count()).select_from(analyzed)),
                "analyzedCompanies": session.scalar(select(func.count(func.distinct(SecDocumentRow.cik))).where(
                    SecDocumentRow.accession.in_(select(analyzed.c.accession)))),
            }
