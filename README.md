# InvestLab AI

Python 3.12 + FastAPI + Pydantic + uv. SQLAlchemy/PyMySQL·Alembic·NumPy를 후속 MySQL/RAG 구현에 사용할 기반 의존성으로 둡니다.

`uv sync --locked`, `uv run --locked pytest`, `uv run --locked ruff check .`로 설치·검사합니다. 32자 이상 랜덤 `INVESTLAB_AI_SERVICE_TOKEN`을 환경에 설정하고 `uv run --locked uvicorn investlab_ai.main:create_app --factory --host 127.0.0.1 --port 8000`으로 실행합니다.

`/health`는 프로세스 liveness입니다. `/internal/v1/questions`는 서버 간 인증을 검사하지만 도구·LLM 미설정으로 503 AI_NOT_CONFIGURED를 반환합니다. 공시 수집·DB·RAG·LLM은 아직 구현하지 않았으며 근거 없는 샘플 분석을 반환하지 않습니다. DB/LLM readiness는 후속 작업입니다.

AI 담당 유광민, 명세·인프라 송상진. 회의 설명과 CI 통과 후 작성자가 자기 PR을 머지할 수 있습니다. 전체 설계·규약은 backend/docs에서 관리합니다. Redis·벡터 DB는 확정하지 않았습니다.

초기 main에는 협업 템플릿과 README만 등록합니다. 앱 코드·전체 설정은 chore/1-initial-scaffold PR에서 검토 후 작성자가 머지할 수 있습니다.
