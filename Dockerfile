FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.22
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev
USER 10001:10001
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "investlab_ai.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
