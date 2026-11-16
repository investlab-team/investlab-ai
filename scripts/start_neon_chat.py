"""Use the linked Neon DB explicitly, without changing normal start-chat settings."""
import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.neon_environment import load_neon_environment
from investlab_ai.storage import database_url

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        load_neon_environment(ROOT)
        settings = Settings()
        results = []
        for direct in (False, True):
            engine = create_engine(database_url(settings, direct=direct), pool_pre_ping=True,
                                   connect_args={"connect_timeout": 15})
            try:
                with engine.connect() as connection:
                    assert connection.scalar(text("SELECT 1")) == 1
                    ready = inspect(connection).has_table("rag_documents")
                    if ready:
                        ready = connection.scalar(text("SELECT count(*) FROM rag_documents")) > 0
                    results.append(ready)
            finally:
                engine.dispose()
        print("Neon pooled/direct 연결 확인 완료. 연결 문자열은 출력하지 않습니다.")
        if args.check:
            print("공유 데이터 준비:", "완료" if all(results) else "아직 이전하지 않음")
            return 0
        if not all(results):
            print("공유 DB의 스키마·기존 분석 데이터 이전이 먼저 필요해요. 로컬 DB는 그대로 유지돼요.")
            return 1
        import start_chat
        sys.argv = [str(ROOT / "scripts/start_chat.py"), "--port", "8002"]
        if args.no_browser:
            sys.argv.append("--no-browser")
        return start_chat.main()
    except ServiceError as exc:
        print("Neon 설정 확인 필요:", exc.code)
    except Exception as exc:
        # A connection exception can embed passwords: report its type only.
        print("Neon 연결 확인 실패:", type(exc).__name__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
