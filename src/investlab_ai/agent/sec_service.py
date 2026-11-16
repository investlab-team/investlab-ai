"""Shared SEC filing evidence and per-filing GPT basic-analysis cache."""
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from investlab_ai.agent.service import ResearchService
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.sec import (
    SEC_PARSER_VERSION,
    SecClient,
    sec_chunks,
    select_sec_evidence,
)
from investlab_ai.object_storage import EvidenceObjects
from investlab_ai.schemas import OverseasCompany, SecFiling
from investlab_ai.sec_storage import SecRepository


class LocalSecObjects:
    encode_chunks = staticmethod(EvidenceObjects.encode_chunks)

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, receipt, kind, data, *, verify=False, namespace="sec"):
        key = f"sec/{receipt}/v1/{kind}/{hashlib.sha256(data).hexdigest()}"
        path = self.root / (hashlib.sha256(key.encode()).hexdigest() + ".blob")
        if not path.exists():
            temporary = path.with_suffix("." + uuid4().hex[:8] + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        if verify:
            self.get(key)
        return key

    def get(self, key):
        data = (self.root / (hashlib.sha256(key.encode()).hexdigest() + ".blob")).read_bytes()
        if hashlib.sha256(data).hexdigest() != key.rsplit("/", 1)[-1]:
            raise ServiceError("EVIDENCE_OBJECT_CORRUPT", 503)
        return data

    def chunks(self, key, receipt, count):
        return EvidenceObjects.decode_chunks(self.get(key), receipt, count)


class SecService:
    def __init__(self, research: ResearchService, client: SecClient | None = None):
        self.research = research
        self.repo = SecRepository(research.repo)
        self.client = client
        self.objects = research.repo.objects or LocalSecObjects(
            research.config.private_cache_dir / "sec-public-evidence")

    def close(self):
        if self.client:
            self.client.close()

    def require_client(self):
        if self.client is None:
            self.client = SecClient(self.research.config)
        return self.client

    def collect(self, issuer: OverseasCompany, filing: SecFiling):
        existing = self.repo.document(filing.accession)
        if existing:
            return existing
        self.repo.save_issuer(issuer)
        key = hashlib.sha256(("sec-collect:" + filing.accession).encode()).hexdigest()
        owner = str(uuid4())
        if not self.research.repo.claim_analysis(key, owner):
            for _ in range(40):
                time.sleep(0.5)
                existing = self.repo.document(filing.accession)
                if existing:
                    return existing
            raise ServiceError("PREPARATION_IN_PROGRESS", 409)
        try:
            if existing := self.repo.document(filing.accession):
                return existing
            parts = self.require_client().document_parts(filing)
            archive, records, manifest = io.BytesIO(), [], []
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zipped:
                for part in parts:
                    info = zipfile.ZipInfo(part["name"])
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zipped.writestr(info, part["raw"])
                    manifest.append({"name": part["name"], "sourceUrl": part["sourceUrl"],
                                     "sha256": hashlib.sha256(part["raw"]).hexdigest()})
                    for c in sec_chunks(part["raw"], filing.accession + ":" + part["name"]):
                        records.append({"chunk_id": c.chunk_id, "ordinal": len(records), "section": c.section,
                                        "text": c.text, "token_count": c.token_count, "embedding": [],
                                        "source_url": part["sourceUrl"]})
                zipped.writestr(zipfile.ZipInfo("manifest.json"), json.dumps(manifest, ensure_ascii=False))
            raw = archive.getvalue()
            receipt = f"{filing.cik}/{filing.accession}"
            original_key = self.objects.put(receipt, "filing.zip", raw,
                                            namespace="sec", verify=True)
            chunks_key = self.objects.put(receipt, "chunks.json.gz", self.objects.encode_chunks(filing.accession, records),
                                          namespace="sec", verify=True)
            self.repo.save_document(filing, hashlib.sha256(raw).hexdigest(), original_key,
                                    chunks_key, len(records), SEC_PARSER_VERSION)
            return self.repo.document(filing.accession)
        finally:
            self.research.repo.release_analysis(key, owner)

    def records(self, filing: SecFiling):
        row = self.repo.document(filing.accession)
        if row is None:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        return self.objects.chunks(row["chunks_key"], filing.accession, row["chunk_count"])

    def original(self, filing: SecFiling) -> bytes:
        row = self.repo.document(filing.accession)
        if row is None:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        raw = self.objects.get(row["original_key"])
        if hashlib.sha256(raw).hexdigest() != row["content_hash"]:
            raise ServiceError("EVIDENCE_OBJECT_CORRUPT", 503)
        return raw

    def cache_key(self, filing: SecFiling):
        row = self.repo.document(filing.accession)
        if row is None:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        return hashlib.sha256(f"sec-basic-v1:{filing.accession}:{row['content_hash']}:"
                              f"{row['parser_version']}:{self.research.config.openai_model}:"
                              f"{self.research.config.sec_basic_analysis_tokens}".encode()).hexdigest()

    def basic(self, filing: SecFiling) -> dict:
        key = self.cache_key(filing)
        if cached := self.repo.analysis(key):
            return {**cached, "cached": True, "modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
        provider = self.research.require_ai()
        owner = str(uuid4())
        if not self.research.repo.claim_analysis(key, owner):
            raise ServiceError("ANALYSIS_IN_PROGRESS", 409)
        try:
            if cached := self.repo.analysis(key):
                return {**cached, "cached": True, "modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
            evidence = select_sec_evidence(self.records(filing), filing, self.research.config.sec_basic_analysis_tokens)
            with self.research.model_lock:
                draft = provider.generate_sec(
                    f"{filing.issuer_name} ({filing.symbol})의 {filing.form}, 공시일 {filing.filed_on}, "
                    f"보고대상일 {filing.report_date}에 대한 기본 분석입니다. 사업·실적·위험·확인사항을 한국어로 설명하세요. "
                    "제공된 발췌만 분석했으며 전체 문서를 검토했다고 말하지 마세요.", evidence)
                self.research.validate_draft(draft, evidence)
                usage = getattr(provider, "last_usage", {})
            used = {identity for c in draft.claims for identity in c.evidence_ids}
            result = {"status": draft.status, "answer": "\n\n".join(c.text for c in draft.claims),
                      "claims": [c.model_dump(by_alias=True) for c in draft.claims],
                      "sources": [e.model_dump(by_alias=True) for e in evidence if e.chunk_id in used],
                      "model": self.research.config.openai_model, "modelCalls": 1, "cached": False,
                      "generatedAt": datetime.now(UTC).isoformat(), "analysisScope": "basic",
                      "inputTokens": usage.get("input_tokens", 0), "outputTokens": usage.get("output_tokens", 0),
                      "warnings": ["SEC 정기보고서 핵심 발췌의 기본 분석입니다. 전체 정밀 분석은 아닙니다.",
                                   "달러·주식수·주당금액 및 분기/누적/회계연도 구분은 원문을 함께 확인하세요.",
                                   "40-F의 HTML 재무 첨부는 함께 보관하며 PDF·이미지 첨부 및 외부 링크는 분석하지 않습니다."]}
            self.repo.save_analysis(key, filing.accession, result)
            return result
        finally:
            self.research.repo.release_analysis(key, owner)

    def ask(self, filing: SecFiling, question: str) -> dict:
        # Bounded lexical retrieval over complete archived chunks, with bilingual finance terms.
        mapping = {"매출": "revenue sales", "이익": "income profit earnings", "현금": "cash flows liquidity",
                   "부채": "debt liabilities", "위험": "risk uncertainty", "왜": "management discussion results",
                   "배당": "dividend repurchase", "성장": "growth demand", "인공지능": "artificial intelligence AI",
                   "투자": "capital expenditures investments", "사업": "business products segments"}
        terms = set(re.findall(r"[a-zA-Z]{2,}", question.lower()))
        for korean, english in mapping.items():
            if korean in question:
                terms.update(english.lower().split())
        records = self.records(filing)
        if terms:
            records = sorted(records, key=lambda r: -sum(
                len(re.findall(r"\b" + re.escape(t) + r"\b", r["section"] + " " + r["text"], re.I)) for t in terms))
            from investlab_ai.schemas import Evidence
            evidence, budget = [], 0
            for row in records:
                if budget + row["token_count"] + 180 > self.research.config.sec_basic_analysis_tokens:
                    continue
                evidence.append(Evidence(chunk_id=row["chunk_id"], section=row["section"], text=row["text"], source_url=row.get("source_url", filing.source_url)))
                budget += row["token_count"] + 180
                if len(evidence) >= 16:
                    break
        else:
            evidence = select_sec_evidence(records, filing, self.research.config.sec_basic_analysis_tokens)
        provider = self.research.require_ai()
        with self.research.model_lock:
            draft = provider.generate_sec(question, evidence)
            self.research.validate_draft(draft, evidence)
            usage = getattr(provider, "last_usage", {})
        used = {i for c in draft.claims for i in c.evidence_ids}
        return {"status": draft.status, "answer": "\n\n".join(c.text for c in draft.claims),
                "claims": [c.model_dump(by_alias=True) for c in draft.claims],
                "sources": [e.model_dump(by_alias=True) for e in evidence if e.chunk_id in used],
                "model": self.research.config.openai_model, "modelCalls": 1, "cached": False,
                "generatedAt": datetime.now(UTC).isoformat(),
                "inputTokens": usage.get("input_tokens", 0), "outputTokens": usage.get("output_tokens", 0),
                "analysisScope": "question", "warnings": ["SEC 원문의 제한된 검색 근거에 기반한 답변입니다."]}
