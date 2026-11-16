from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class APIModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Company(APIModel):
    company_id: str
    corp_code: str = Field(pattern=r"^\d{8}$")
    corp_name: str
    stock_code: str | None = None


class OverseasCompany(APIModel):
    company_id: str
    corp_code: None = None
    corp_name: str
    stock_code: str
    cik: str | None = Field(default=None, pattern=r"^\d{10}$")
    market: str = "US"
    currency: Literal["USD"] = "USD"
    aliases: list[str] = Field(default_factory=list)


class SecFiling(APIModel):
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    cik: str = Field(pattern=r"^\d{10}$")
    symbol: str
    issuer_name: str
    form: str
    filed_on: date
    accepted_at: datetime | None = None
    report_date: date | None = None
    primary_document: str
    source_url: str


class Disclosure(APIModel):
    rcept_no: str = Field(pattern=r"^\d{14}$")
    corp_code: str = Field(pattern=r"^\d{8}$")
    corp_name: str
    report_name: str
    filed_on: date
    source_url: str
    remarks: str = ""


class PreparedDocument(APIModel):
    document_id: str
    disclosure: Disclosure
    chunk_count: int
    embedding_model: str
    content_hash: str
    warnings: list[str]


class DisclosurePage(APIModel):
    items: list[Disclosure]
    page: int
    total_pages: int


class Question(APIModel):
    question: str = Field(min_length=1, max_length=4000)
    rcept_no: str | None = Field(default=None, pattern=r"^\d{14}$")

    @field_validator("question")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("질문을 입력하세요.")
        return value.strip()


class AnalysisRequest(APIModel):
    max_model_calls: int = Field(default=10, ge=1, le=100)


class AnalysisPlan(APIModel):
    rcept_no: str
    chunk_count: int
    model_calls: int
    estimated_evidence_tokens: int


class Evidence(APIModel):
    chunk_id: str
    section: str
    text: str
    source_url: str


class Claim(APIModel):
    category: Literal["summary", "business", "performance", "risk", "follow_up"]
    text: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class Draft(APIModel):
    status: Literal["answered", "insufficient_evidence"]
    claims: list[Claim] = Field(max_length=12)


class Answer(APIModel):
    status: Literal["answered", "insufficient_evidence"]
    rcept_no: str
    answer: str
    claims: list[Claim]
    sources: list[Evidence]
    model: str
    model_calls: int
    warnings: list[str]
    cached: bool = False
    generated_at: datetime | None = None
    analysis_scope: str = "question"
    input_tokens: int = 0
    output_tokens: int = 0


class ChatRequest(APIModel):
    message: str = Field(min_length=1, max_length=4000)
    rcept_no: str | None = Field(default=None, pattern=r"^\d{14}$")
    corp_code: str | None = Field(default=None, pattern=r"^\d{8}$")
    stock_symbol: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")
    previous_task_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("질문을 입력하세요.")
        return value.strip()
