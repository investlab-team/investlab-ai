from pathlib import Path

import pytest

from investlab_ai.config import DatabaseSettings, Settings
from investlab_ai.errors import ServiceError
from investlab_ai.neon_environment import load_neon_environment
from investlab_ai.storage import database_url

TOKEN = "test-only-service-token-neon-123456789"
POOLED = "postgresql://test:fake-password@ep-example-pooler.test.neon.tech/neondb?sslmode=require"
DIRECT = "postgresql://test:fake-password@ep-example.test.neon.tech/neondb?sslmode=require"


def test_neon_urls_keep_psycopg_and_separate_pooled_from_direct(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POOLED)
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", DIRECT)
    settings = Settings(service_token=TOKEN)
    pooled, direct = database_url(settings), database_url(settings, direct=True)
    assert pooled.drivername == direct.drivername == "postgresql+psycopg"
    assert pooled.host == "ep-example-pooler.test.neon.tech"
    assert direct.host == "ep-example.test.neon.tech"
    assert pooled.query["sslmode"] == "require"
    assert "fake-password" not in str(pooled)
    assert "fake-password" not in repr(DatabaseSettings())


def test_migration_cannot_silently_use_neon_transaction_pooler(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POOLED)
    monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)
    with pytest.raises(ServiceError, match="DIRECT_DATABASE_URL_REQUIRED"):
        database_url(Settings(service_token=TOKEN), direct=True)


def test_explicit_local_sqlite_still_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", POOLED)
    assert database_url(Settings(service_token=TOKEN, local_sqlite_path=tmp_path / "local.db")).drivername == "sqlite+pysqlite"


def test_neon_profile_loads_secret_file_without_rewriting_existing_settings(monkeypatch, tmp_path):
    for key in ("DATABASE_URL", "RESEARCH_DB_URL", "INVESTLAB_AI_SERVICE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    # Track environment mutations so this profile cannot leak into other tests.
    for key in ("INVESTLAB_AI_LOCAL_SQLITE_PATH", "INVESTLAB_AI_USE_DOCKER_DB", "INVESTLAB_AI_ENABLE_LOCAL_CHAT"):
        monkeypatch.setenv(key, "false")
    original = "INVESTLAB_AI_SERVICE_TOKEN=" + TOKEN + "\nINVESTLAB_AI_USE_DOCKER_DB=true\n"
    (tmp_path / ".env").write_text(original)
    (tmp_path / ".env.local").write_text("DATABASE_URL=" + POOLED + "\n")
    load_neon_environment(tmp_path)
    assert database_url(Settings()).host == "ep-example-pooler.test.neon.tech"
    assert not Settings().use_docker_db and Settings().enable_local_chat
    assert Settings().local_sqlite_path is None
    assert (tmp_path / ".env").read_text() == original
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INVESTLAB_AI_SERVICE_TOKEN", raising=False)


def test_missing_neon_file_is_reported_without_a_connection_string(tmp_path):
    with pytest.raises(ServiceError, match="NEON_ENV_FILE_MISSING"):
        load_neon_environment(Path(tmp_path))
