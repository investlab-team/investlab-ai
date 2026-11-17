"""Start Docker DB, migrate schema/data, then switch .env only after verification."""
import json
import sqlite3
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values, load_dotenv, set_key, unset_key
from sqlalchemy import create_engine

from investlab_ai.config import Settings
from investlab_ai.database_transfer import transfer
from investlab_ai.local_database import (
    DockerDatabase,
    LocalDatabaseError,
    app_ports_busy,
    prepare_environment,
)
from investlab_ai.storage import database_url

ROOT = Path(__file__).resolve().parents[1]


def main():
    db, started = None, False
    source_engine, target_engine = None, None
    try:
        if app_ports_busy():
            raise LocalDatabaseError("먼저 채팅 서버를 Ctrl+C로 종료하고 다시 실행하세요.")
        env_file = prepare_environment(ROOT)
        values = dotenv_values(env_file)
        load_dotenv(env_file, override=True)
        settings = Settings(local_sqlite_path=None, enable_local_chat=True, use_docker_db=True)
        db = DockerDatabase(ROOT)
        started = db.start()
        # Alembic receives a validated URL directly, independent of old SQLite env vars.
        alembic = Config(str(ROOT / "alembic.ini"))
        alembic.set_main_option("script_location", str(ROOT / "migrations"))
        target_engine = create_engine(database_url(settings), pool_pre_ping=True)
        with target_engine.connect() as connection:
            alembic.attributes["connection"] = connection
            command.upgrade(alembic, "head")
        source_path = ROOT / (values.get("INVESTLAB_AI_LOCAL_SQLITE_PATH") or ".local/research.db")
        if source_path.is_file():
            source_engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(
                source_path.resolve().as_uri() + "?mode=ro", uri=True))
            print("원문과 기존 분석을 이전하고 있어요. DART/GPT 재호출은 하지 않아요.", flush=True)
            report = transfer(source_engine, target_engine,
                              emit=lambda item: print(json.dumps(item), flush=True))
            (ROOT / ".local/postgres-transfer.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        unset_key(env_file, "INVESTLAB_AI_LOCAL_SQLITE_PATH")
        set_key(env_file, "INVESTLAB_AI_ENABLE_LOCAL_CHAT", "true")
        set_key(env_file, "INVESTLAB_AI_USE_DOCKER_DB", "true")
        print("PostgreSQL 전환 완료! 앞으로 start-chat.cmd로 DB와 채팅을 함께 켜세요.")
        return 0
    except Exception as exc:
        # SQL errors can embed raw JSON or secrets. Never print exceptions from a driver.
        print(str(exc) if isinstance(exc, LocalDatabaseError) else
              f"전환을 완료하지 못했어요({type(exc).__name__}). 기존 SQLite와 설정 백업은 유지돼요.")
        return 1
    finally:
        if source_engine:
            source_engine.dispose()
        if target_engine:
            target_engine.dispose()
        if db and started:
            try:
                db.stop()
            except LocalDatabaseError:
                print("DB 종료 상태는 Docker Desktop에서 확인하세요.")


if __name__ == "__main__":
    sys.exit(main())
