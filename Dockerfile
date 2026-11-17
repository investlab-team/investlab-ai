FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.22
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-dev
RUN mkdir -p /app/.local/private && chown -R 10001:10001 /app/.local
USER 10001:10001
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "investlab_ai.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
