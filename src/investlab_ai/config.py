from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVESTLAB_AI_", hide_input_in_errors=True)
    service_token: str = Field(min_length=32)
    dart_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    embedding_model: str = "text-embedding-3-small"
    local_sqlite_path: Path | None = None
    enable_docs: bool = False
    provider_timeout_seconds: float = Field(default=90, gt=0, le=180)
    chunk_tokens: int = Field(default=800, ge=100, le=2000)
    max_document_chunks: int = Field(default=15000, ge=1, le=50000)
    retrieval_limit: int = Field(default=8, ge=1, le=20)
    analysis_batch_tokens: int = Field(default=18000, ge=2000, le=30000)
    max_analysis_calls: int = Field(default=40, ge=1, le=100)
    max_output_tokens: int = Field(default=5000, ge=1000, le=16000)
    enable_local_chat: bool = False
    use_docker_db: bool = False
    instance_id: str = Field(default="", max_length=100)
    object_bucket: str | None = None
    toss_client_id: SecretStr | None = None
    toss_client_secret: SecretStr | None = None
    private_cache_dir: Path = Path(".local/private")
    toss_requests_per_second: float = Field(default=5, gt=0, le=15)
    toss_quote_cache_seconds: int = Field(default=15, ge=1, le=60)
    toss_candle_cache_seconds: int = Field(default=300, ge=30, le=3600)
    sec_user_agent: str | None = Field(default=None, repr=False)
    sec_requests_per_second: float = Field(default=2, gt=0, le=5)
    sec_basic_analysis_tokens: int = Field(default=10000, ge=2000, le=30000)
    basic_analysis_tokens: int = Field(default=16000, ge=2000, le=30000)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RESEARCH_DB_", populate_by_name=True,
                                     hide_input_in_errors=True)
    connection_url: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("RESEARCH_DB_URL", "DATABASE_URL"),
    )
    direct_url: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("RESEARCH_DB_DIRECT_URL", "DATABASE_URL_UNPOOLED"),
    )
    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "investlab_research"
    username: str = "investlab_research"
    password: SecretStr = SecretStr("")
