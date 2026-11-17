"""Authenticated SEC research routes for the team's backend integration."""
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import Field

from investlab_ai.agent.overseas_chat import normalized, resolve_overseas
from investlab_ai.agent.sec_service import SecService
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.catalog import years_ago
from investlab_ai.market import NEW_YORK
from investlab_ai.schemas import APIModel, Claim, Evidence, OverseasCompany, SecFiling

Accession = Annotated[str, Path(pattern=r"^\d{10}-\d{2}-\d{6}$")]
Cik = Annotated[str, Path(pattern=r"^\d{10}$")]


class SecQuestion(APIModel):
    question: str = Field(min_length=1, max_length=4000, pattern=r"\S")


class SecPrepared(APIModel):
    filing: SecFiling
    chunk_count: int
    content_hash: str
    parser_version: str


class SecAnswer(APIModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    claims: list[Claim]
    sources: list[Evidence]
    model: str
    model_calls: int
    cached: bool
    analysis_scope: Literal["basic", "question"]
    generated_at: datetime | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str]


class SecResponse(APIModel):
    filing: SecFiling
    result: SecAnswer


def sec_routes(service: SecService, common: dict) -> APIRouter:
    router = APIRouter(prefix="/internal/v1")

    def catalog():
        stored = service.repo.issuers()
        return service.require_client().companies() if service.research.config.sec_user_agent else stored

    def issuer(cik):
        if stored := service.repo.issuer(cik):
            return stored
        match = next((c for c in catalog() if c.cik == cik), None)
        if not match:
            raise ServiceError("SEC_COMPANY_NOT_FOUND", 404)
        return match

    def prepared(accession):
        row = service.repo.document(accession)
        if not row:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        return row, SecFiling.model_validate(row["data"])

    @router.get("/overseas-companies", response_model=list[OverseasCompany], **common)
    def companies(query: str = Query(min_length=1, max_length=100)):
        stored = service.repo.issuers()
        if matches := resolve_overseas(query, stored):
            return matches[:20]
        rows = catalog()
        matches = resolve_overseas(query, rows)
        return (matches or [r for r in rows if normalized(query) in normalized(r.corp_name)])[:20]

    @router.get("/overseas-companies/{cik}/disclosures", response_model=list[SecFiling], **common)
    def filings(cik: Cik, start: Annotated[date | None, Query(alias="startDate")] = None,
                end: Annotated[date | None, Query(alias="endDate")] = None):
        today = datetime.now(NEW_YORK).date()
        start, end = start or years_ago(today), end or today
        if start > end or start < years_ago(today) or end > today:
            raise ServiceError("INVALID_DATE_RANGE", 422)
        company = issuer(cik)
        if service.research.config.sec_user_agent:
            return service.require_client().filings(company, start, end)
        return [f for f in service.repo.filings(cik) if start <= f.filed_on <= end]

    @router.post("/sec-disclosures/{accession}/prepare", response_model=SecPrepared, **common)
    def prepare(accession: Accession, cik: str = Query(pattern=r"^\d{10}$")):
        row = service.repo.document(accession)
        if row:
            filing = SecFiling.model_validate(row["data"])
            if filing.cik != cik:
                raise ServiceError("SEC_FILING_COMPANY_MISMATCH", 422)
        else:
            company = issuer(cik)
            today = datetime.now(NEW_YORK).date()
            found = service.require_client().filings(company, years_ago(today), today)
            filing = next((f for f in found if f.accession == accession), None)
            if not filing:
                raise ServiceError("SEC_DOCUMENT_NOT_FOUND", 404)
            # The source URL is derived from SEC metadata, never accepted from the caller.
            row = service.collect(company, filing)
        return SecPrepared(filing=filing, chunk_count=row["chunk_count"], content_hash=row["content_hash"],
                           parser_version=row["parser_version"])

    @router.post("/sec-disclosures/{accession}/basic-analysis", response_model=SecResponse, **common)
    def basic(accession: Accession):
        _, filing = prepared(accession)
        return {"filing": filing, "result": service.basic(filing)}

    @router.post("/sec-disclosures/{accession}/questions", response_model=SecResponse, **common)
    def question(accession: Accession, body: SecQuestion):
        _, filing = prepared(accession)
        return {"filing": filing, "result": service.ask(filing, body.question)}

    return router
