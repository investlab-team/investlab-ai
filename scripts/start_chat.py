"""Double-click entry point: start the local service and open the conversation UI."""
import argparse
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

from investlab_ai.config import Settings
from investlab_ai.local_database import DockerDatabase, LocalDatabaseError

ROOT = Path(__file__).resolve().parents[1]


def serve(port, open_browser):
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / ".env.toss", override=False)
    load_dotenv(ROOT / ".env.sec", override=False)
    config = Settings()
    db, started, process = None, False, None
    try:
        if config.use_docker_db and config.local_sqlite_path is None:
            db = DockerDatabase(ROOT)
            started = db.start()
        url = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "investlab_ai.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--env-file", ".env",
        ], cwd=ROOT)
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(80):
                if process.poll() is not None:
                    return 1
                try:
                    if client.get(url + "/health").status_code == 200:
                        if open_browser:
                            webbrowser.open(url)
                        print("채팅 화면:", url, "\n이 창을 열어두세요. 종료: Ctrl+C")
                        process.wait()
                        return 0
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
        print("서버 시작을 확인하지 못했어요. 위 오류 내용을 확인하세요.")
        return 1
    except KeyboardInterrupt:
        return 0
    except LocalDatabaseError as exc:
        print(str(exc))
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if db and started:
            try:
                db.stop()
            except LocalDatabaseError:
                print("PostgreSQL 종료는 stop-database.cmd 또는 Docker Desktop에서 확인하세요.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, choices=(8000, 8001, 8002))
    args = parser.parse_args()
    if not (ROOT / ".env").exists():
        print("먼저 scripts/setup.ps1을 실행하고 .env에 API 키를 설정하세요.")
        return 1
    with httpx.Client(timeout=1, trust_env=False) as client:
        for port in ((args.port,) if args.port else (8000, 8001, 8002)):
            url = f"http://127.0.0.1:{port}"
            with socket.socket() as probe:
                occupied = probe.connect_ex(("127.0.0.1", port)) == 0
            if occupied:
                try:
                    page = client.get(url)
                    if page.status_code == 200 and "/chat-assets/chat.js" in page.text:
                        if not args.no_browser:
                            webbrowser.open(url)
                        print("이미 실행 중인 채팅 화면을 열었어요:", url)
                        return 0
                except httpx.HTTPError:
                    pass
                continue
            return serve(port, not args.no_browser)
        print("8000~8002 포트를 사용할 수 없어요. 기존 서버를 종료한 뒤 다시 실행해 주세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
