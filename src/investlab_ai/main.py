import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from investlab_ai.config import Settings


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    app = FastAPI(title="InvestLab Internal AI", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {config.service_token}"
        if authorization is None or not secrets.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="INVALID_SERVICE_CREDENTIALS")

    @app.get("/health")
    def health() -> dict[str, str]:
        # Liveness only. Research DB / LLM readiness is not implemented yet.
        return {"status": "ok"}

    @app.post("/internal/v1/questions", dependencies=[Depends(authenticate)])
    def question(body: Question) -> None:
        # Do not invent an analysis before tools and evidence providers exist.
        raise HTTPException(status_code=503, detail="AI_NOT_CONFIGURED")

    return app
