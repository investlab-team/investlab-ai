import hashlib
import time
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.dart import DartClient
from investlab_ai.ingestion.overview import overview_evidence
from investlab_ai.ingestion.parser import parse_document, tokens
from investlab_ai.providers.openai_provider import Provider, validate_vectors
from investlab_ai.retrieval.basic import select_basic_evidence
from investlab_ai.retrieval.search import retrieve
from investlab_ai.schemas import AnalysisPlan, Answer, Disclosure, Draft, Evidence, PreparedDocument
from investlab_ai.storage import PARSER_VERSION, Repository

ANALYSIS_VERSION = "basic-overview-v2"

INSUFFICIENT = "선택한 공시의 제공된 근거에서 질문에 대한 답을 확인할 수 없습니다."


class ResearchService:
    def __init__(
        self, config: Settings, repo: Repository, dart: DartClient | None,
        provider: Provider | None,
    ):
        self.config, self.repo, self.dart, self.provider = config, repo, dart, provider
        # A single-process MVP: prevent duplicate preparation charges in one worker.
        self.prepare_lock = Lock()
        self.model_lock = Lock()

    def require_ai(self) -> Provider:
        if self.provider is None:
            raise ServiceError("AI_NOT_CONFIGURED", 503)
        return self.provider

    def require_dart(self) -> DartClient:
        if self.dart is None:
            raise ServiceError("DART_NOT_CONFIGURED", 503)
        return self.dart

    def prepare(
        self, receipt: str, build_embeddings: bool = True,
        disclosure: Disclosure | None = None,
    ) -> PreparedDocument:
        provider = self.require_ai()
        dart = self.require_dart()
        if not self.prepare_lock.acquire(blocking=False):
            raise ServiceError("PREPARATION_IN_PROGRESS", 409)
        try:
            cached = self.repo.document(receipt)
            if cached:
                if cached.chunk_count == 0:
                    chunks = parse_document(self.repo.original(receipt), receipt, self.config.chunk_tokens)
                    if len(chunks) > self.config.max_document_chunks and build_embeddings:
                        raise ServiceError("DOCUMENT_TOO_LARGE", 413)
                    self.repo.populate_chunks(receipt, chunks)
                    cached = self.repo.document(receipt)
                if cached.embedding_model and cached.embedding_model != self.config.embedding_model:
                    raise ServiceError("EMBEDDING_MODEL_MISMATCH", 409)
                if build_embeddings and not cached.embedding_model:
                    rows = self.repo.chunks(receipt)
                    vectors = provider.embed([row[0].text for row in rows])
                    validate_vectors(vectors, len(rows))
                    self.repo.index_document(receipt, self.config.embedding_model, vectors)
                    return self.repo.document(receipt)
                return cached
            disclosure = disclosure or dart.lookup(receipt)
            if disclosure.rcept_no != receipt:
                raise ServiceError("DISCLOSURE_METADATA_MISMATCH", 422)
            raw = dart.document(receipt)
            chunks = parse_document(raw, receipt, self.config.chunk_tokens)
            if len(chunks) > self.config.max_document_chunks and build_embeddings:
                raise ServiceError("DOCUMENT_TOO_LARGE", 413)
            vectors = provider.embed([chunk.text for chunk in chunks]) if build_embeddings else [
                [] for _ in chunks
            ]
            if build_embeddings:
                validate_vectors(vectors, len(chunks))
            warnings = [
                "표의 병합 셀은 span으로 보존하며 복잡한 표는 원문 대조가 필요합니다.",
                "첨부 PDF·이미지 OCR과 별도 첨부 다운로드는 지원하지 않습니다.",
            ]
            if b"viewer-manifest.json" in raw:
                warnings.append("OpenDART 파일 대신 동일 접수번호의 DART 공개 뷰어 본문 스냅샷을 보관했습니다.")
            if "정" in disclosure.remarks or "철" in disclosure.remarks:
                warnings.append("정정 또는 철회 관련 표시가 있습니다. 관련 공시를 확인하세요.")
            return self.repo.save_document(
                disclosure, raw, hashlib.sha256(raw).hexdigest(),
                self.config.embedding_model if build_embeddings else "",
                chunks, vectors, warnings,
            )
        finally:
            self.prepare_lock.release()

    def collect_original(self, disclosure: Disclosure) -> PreparedDocument:
        cached = self.repo.document(disclosure.rcept_no)
        if cached:
            return cached
        raw = self.require_dart().document(disclosure.rcept_no)
        return self.repo.save_document(
            disclosure, raw, hashlib.sha256(raw).hexdigest(), "", [], [],
            ["원문 수집 완료. 본문 처리와 기본 분석은 대기 중입니다.", *(
                ["DART 공개 뷰어의 동일 접수번호 본문 스냅샷을 보관했습니다."]
                if b"viewer-manifest.json" in raw else []
            )],
        )

    def _rows(self, receipt: str):
        document = self.repo.document(receipt)
        if document is None:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        if document.embedding_model and document.embedding_model != self.config.embedding_model:
            raise ServiceError("EMBEDDING_MODEL_MISMATCH", 409)
        return self.repo.chunks(receipt)

    @staticmethod
    def validate_draft(draft: Draft, evidence: list[Evidence]) -> None:
        allowed = {item.chunk_id for item in evidence}
        if draft.status == "insufficient_evidence":
            if draft.claims:
                raise ServiceError("LLM_INVALID_RESPONSE")
            return
        if not draft.claims or any(
            not claim.text.strip() or not claim.evidence_ids
            or not set(claim.evidence_ids).issubset(allowed) for claim in draft.claims
        ):
            raise ServiceError("LLM_INVALID_CITATIONS")

    def _answer(
        self, receipt: str, draft: Draft, evidence: list[Evidence], calls: int,
        extra_warnings: list[str] | None = None,
    ) -> Answer:
        self.validate_draft(draft, evidence)
        used = {identity for claim in draft.claims for identity in claim.evidence_ids}
        document = self.repo.document(receipt)
        return Answer(
            status=draft.status, rcept_no=receipt,
            answer="\n\n".join(
                f"[{claim.category}] {claim.text} " +
                " ".join(f"[{identity}]" for identity in claim.evidence_ids)
                for claim in draft.claims
            ) if draft.claims else INSUFFICIENT,
            claims=draft.claims, sources=[item for item in evidence if item.chunk_id in used],
            model=self.config.openai_model or "", model_calls=calls,
            **getattr(self.provider, "last_usage", {}),
            warnings=[*(document.warnings if document else []), *(extra_warnings or []),
                      "인용 ID의 유효성을 검사하지만 내용의 정확성은 원문 대조가 필요합니다."],
        )

    def ask(self, receipt: str, question: str) -> Answer:
        provider = self.require_ai()
        rows = self._rows(receipt)
        if rows and not rows[0][1]:
            raise ServiceError("DOCUMENT_NOT_INDEXED", 409)
        if tokens(question) > 8000:
            raise ServiceError("QUESTION_TOO_LONG_FOR_EMBEDDING", 422)
        if not self.model_lock.acquire(blocking=False):
            raise ServiceError("ANALYSIS_IN_PROGRESS", 409)
        try:
            vectors = provider.embed([question])
            validate_vectors(vectors, 1)
            evidence = retrieve(question, vectors[0], rows, self.config.retrieval_limit)
            if not evidence:
                return self._answer(receipt, Draft(status="insufficient_evidence", claims=[]), [], 0)
            draft = provider.generate(question, evidence)
            return self._answer(receipt, draft, evidence, 1)
        finally:
            self.model_lock.release()

    def _batches(self, receipt: str) -> list[list[Evidence]]:
        batches, current, count = [], [], 0
        for evidence, _, token_count in self._rows(receipt):
            # Allow for source IDs, section labels, and JSON framing.
            cost = token_count + 150
            if current and count + cost > self.config.analysis_batch_tokens:
                batches.append(current)
                current, count = [], 0
            current.append(evidence)
            count += cost
        if current:
            batches.append(current)
        return batches

    def analysis_plan(self, receipt: str) -> AnalysisPlan:
        rows = self._rows(receipt)
        return AnalysisPlan(
            rcept_no=receipt, chunk_count=len(rows), model_calls=len(self._batches(receipt)),
            estimated_evidence_tokens=sum(row[2] + 150 for row in rows),
        )

    def _cache_key(self, receipt: str, scope: str) -> str:
        document = self.repo.document(receipt)
        if document is None:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        identity = (f"{receipt}:{document.content_hash}:{PARSER_VERSION}:"
                    f"{self.config.openai_model}:{ANALYSIS_VERSION}:{scope}:"
                    f"{self.config.basic_analysis_tokens}")
        return hashlib.sha256(identity.encode()).hexdigest()

    def cached_analysis(self, receipt: str, scope: str = "basic") -> Answer | None:
        result = self.repo.analysis(self._cache_key(receipt, scope))
        return result.model_copy(update={"cached": True, "model_calls": 0,
                                         "input_tokens": 0, "output_tokens": 0}) if result else None

    def basic_analyze(self, receipt: str) -> Answer:
        cached = self.cached_analysis(receipt)
        if cached:
            return cached
        provider = self.require_ai()
        # Wait and recheck so concurrent users share one generated analysis per process.
        with self.model_lock:
            cached = self.cached_analysis(receipt)
            if cached:
                return cached
            key, owner = self._cache_key(receipt, "basic"), str(uuid4())
            if not self.repo.claim_analysis(key, owner):
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    cached = self.cached_analysis(receipt)
                    if cached:
                        return cached
                    time.sleep(0.5)
                raise ServiceError("ANALYSIS_IN_PROGRESS", 409)
            try:
                cached = self.cached_analysis(receipt)
                if cached:
                    return cached
                rows = self.repo.chunks(receipt)
                evidence = (select_basic_evidence(rows, self.config.basic_analysis_tokens) if rows else
                            overview_evidence(self.repo.original(receipt), receipt,
                                              self.config.basic_analysis_tokens))
                draft = provider.generate(
                    "사용자에게 제공할 공시 기본 분석입니다. 선택된 핵심 발췌에 근거해 "
                    "사업, 주요 실적, 위험요인, 추가 확인사항을 쉽게 설명하세요. "
                    "이 발췌만 읽었으므로 보고서 전체를 검토했다고 말하지 마세요.", evidence,
                )
                result = self._answer(receipt, draft, evidence, 1, [
                    "사업·실적·위험 등 핵심 발췌에 기반한 기본 분석이며 전체 문서의 정밀 분석은 아닙니다.",
                ])
                result = result.model_copy(update={
                    "generated_at": datetime.now(UTC), "analysis_scope": "basic",
                })
                self.repo.save_analysis(key, result)
                return result
            finally:
                self.repo.release_analysis(key, owner)

    def analyze(self, receipt: str, max_calls: int) -> Answer:
        cached = self.cached_analysis(receipt, "full")
        if cached:
            return cached
        provider = self.require_ai()
        batches = self._batches(receipt)
        if len(batches) > min(max_calls, self.config.max_analysis_calls):
            # Check before any billable call. Never quietly truncate a whole-report analysis.
            raise ServiceError("ANALYSIS_CALL_BUDGET_EXCEEDED", 422)
        if not self.model_lock.acquire(blocking=False):
            raise ServiceError("ANALYSIS_IN_PROGRESS", 409)
        try:
            claims, evidence, insufficient = [], [], 0
            for batch in batches:
                draft = provider.generate(
                    "공시 전체 분석을 위한 부분 분석입니다. 제공된 모든 문단·표를 읽고 "
                    "핵심 요약, 사업, 실적, 위험요인, 확인사항을 근거와 함께 정리하세요.", batch,
                )
                self.validate_draft(draft, batch)
                claims.extend(draft.claims)
                evidence.extend(batch)
                insufficient += draft.status == "insufficient_evidence"
            # Compose validated section summaries without an additional unsupported synthesis.
            draft = Draft.model_construct(
                status="answered" if claims else "insufficient_evidence", claims=claims,
            )
            warnings = ["전체 문서를 순서대로 읽은 부분별 분석을 합친 결과입니다."]
            if insufficient:
                warnings.append(f"{insufficient}개 부분에서는 분석할 근거가 부족했습니다.")
            result = self._answer(receipt, draft, evidence, len(batches), warnings)
            result = result.model_copy(update={
                "generated_at": datetime.now(UTC), "analysis_scope": "full",
            })
            self.repo.save_analysis(self._cache_key(receipt, "full"), result)
            return result
        finally:
            self.model_lock.release()
