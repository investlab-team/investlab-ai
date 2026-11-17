import secrets
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError

from investlab_ai.agent.chat import ChatService
from investlab_ai.agent.sec_service import SecService
from investlab_ai.agent.service import ResearchService
from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.dart import DartClient
from investlab_ai.local_web import local_routes
from investlab_ai.providers.openai_provider import OpenAIProvider
from investlab_ai.schemas import (
    AnalysisPlan,
    AnalysisRequest,
    Answer,
    Company,
    DisclosurePage,
    PreparedDocument,
    Question,
)
from investlab_ai.sec_api import sec_routes
from investlab_ai.storage import Repository

Receipt = Annotated[str, Path(pattern=r"^\d{14}$")]


def create_app(
    settings: Settings | None = None, service: ResearchService | None = None,
) -> FastAPI:
    config = settings or Settings()
    owned_service = service is None
    if service is None:
        repo = Repository(config)
        dart = DartClient(config.dart_api_key.get_secret_value()) if config.dart_api_key else None
        provider = OpenAIProvider(config) if config.openai_api_key and config.openai_model else None
        service = ResearchService(config, repo, dart, provider)
    research = service
    sec = SecService(research)
    chat = ChatService(research) if (config.local_sqlite_path is not None or config.enable_local_chat) else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owned_service and config.local_sqlite_path is not None:
            research.repo.initialize_local()
        if owned_service and chat:
            research.repo.interrupt_chat_tasks()
        yield
        if chat:
            chat.close()
        sec.close()
        if owned_service:
            if research.dart:
                research.dart.close()
            if research.provider:
                research.provider.close()
            research.repo.close()

    app = FastAPI(
        title="InvestLab Internal AI", version="0.3.0", lifespan=lifespan,
        docs_url="/docs" if config.enable_docs else None, redoc_url=None,
        openapi_url="/openapi.json" if config.enable_docs else None,
    )
    if chat:
        app.include_router(local_routes(chat, config.service_token))

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.code})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "RESEARCH_DB_UNAVAILABLE"})

    bearer = HTTPBearer(auto_error=False)

    def service_auth(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        expected = f"Bearer {config.service_token}"
        authorization = request.headers.get("authorization")
        if authorization is None or not secrets.compare_digest(
            authorization.encode(), expected.encode(),
        ):
            raise HTTPException(status_code=401, detail="INVALID_SERVICE_CREDENTIALS")

    common = {
        "dependencies": [Depends(service_auth)],
        "responses": {
            401: {"description": "INVALID_SERVICE_CREDENTIALS"},
            404: {"description": "공시 또는 원문 없음"},
            409: {"description": "준비 상태, 모델 불일치, 동시 요청 충돌"},
            429: {"description": "DART/OpenAI 호출 제한"},
            413: {"description": "공시/ZIP 크기 또는 문서 조각 수 제한 초과"},
            502: {"description": "외부 응답 또는 인용 검증 실패"},
            503: {"description": "서비스 미설정 또는 DB 사용 불가"},
            504: {"description": "외부 서비스 제한시간 초과"},
        },
    }
    app.include_router(sec_routes(sec, common))

    @app.get("/health")
    def health() -> dict[str, str]:
        """Process liveness only; does not promise DB/provider readiness."""
        return {"status": "ok"}

    @app.post("/internal/v1/companies/sync", **common)
    def sync_companies() -> dict[str, int]:
        companies = research.require_dart().companies()
        research.repo.save_companies(companies)
        return {"companyCount": len(companies)}

    @app.get("/internal/v1/companies", response_model=list[Company], **common)
    def companies(query: str = Query(min_length=1, max_length=100)):
        if not research.repo.has_companies():
            raise ServiceError("COMPANY_CATALOG_NOT_READY", 409)
        return research.repo.search_companies(query)

    @app.get("/internal/v1/disclosures", response_model=DisclosurePage, **common)
    def disclosures(
        corp_code: Annotated[str, Query(alias="corpCode", pattern=r"^\d{8}$")],
        start: Annotated[date, Query(alias="startDate")],
        end: Annotated[date, Query(alias="endDate")],
        page: int = Query(default=1, ge=1, le=10000),
        final_only: bool = Query(default=False, alias="finalOnly"),
    ):
        if start > end:
            raise ServiceError("INVALID_DATE_RANGE", 422)
        return research.require_dart().disclosures(corp_code, start, end, page, final_only=final_only)

    @app.post(
        "/internal/v1/disclosures/{rcept_no}/prepare",
        response_model=PreparedDocument, **common,
    )
    def prepare(rcept_no: Receipt):
        return research.prepare(rcept_no)

    @app.get(
        "/internal/v1/disclosures/{rcept_no}", response_model=PreparedDocument, **common,
    )
    def document(rcept_no: Receipt):
        result = research.repo.document(rcept_no)
        if result is None:
            raise ServiceError("DOCUMENT_NOT_PREPARED", 409)
        return result

    @app.get(
        "/internal/v1/disclosures/{rcept_no}/analysis-plan",
        response_model=AnalysisPlan, **common,
    )
    def plan(rcept_no: Receipt):
        return research.analysis_plan(rcept_no)

    @app.post(
        "/internal/v1/disclosures/{rcept_no}/analyze", response_model=Answer, **common,
    )
    def analyze(rcept_no: Receipt, body: AnalysisRequest):
        return research.analyze(rcept_no, body.max_model_calls)

    @app.post("/internal/v1/questions", response_model=Answer, **common)
    def question(body: Question):
        research.require_ai()
        if body.rcept_no is None:
            raise ServiceError("DISCLOSURE_SELECTION_REQUIRED", 422)
        return research.ask(body.rcept_no, body.question)

    return app
