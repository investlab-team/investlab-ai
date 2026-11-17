"""Docker PostgreSQL lifecycle for this development checkout only."""
import argparse
import os
import secrets
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key


class LocalDatabaseError(RuntimeError):
    pass


def docker_executable():
    executable = shutil.which("docker")
    if executable:
        return executable
    for directory in (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker/Docker",
                      Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop"):
        candidate = directory / "resources/bin/docker.exe"
        if candidate.is_file():
            return str(candidate)
    raise LocalDatabaseError(
        "Docker Desktop이 필요해요. WSL 2와 Docker Desktop을 설치·실행한 뒤 다시 시도하세요.")


class DockerDatabase:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.command = [docker_executable(), "compose", "--project-name", "investlab-ai-local",
                        "--file", str(self.root / "compose.yaml"),
                        "--env-file", str(self.root / ".env")]

    def run(self, *args, timeout=300):
        try:
            result = subprocess.run([*self.command, *args], cwd=self.root, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalDatabaseError("Docker 명령을 완료하지 못했어요. Docker Desktop 상태를 확인하세요.") from exc
        if result.returncode:
            raise LocalDatabaseError(
                "PostgreSQL 컨테이너를 실행하지 못했어요. Docker Desktop이 실행 중인지 확인하고 "
                "VS Code 터미널에서 docker compose ps로 상태를 확인하세요.")
        return result.stdout

    def start(self):
        running = bool(self.run("ps", "--status", "running", "--quiet", "postgres").strip())
        print("PostgreSQL 컨테이너를 확인하고 있어요. 최초 실행은 이미지 다운로드로 시간이 걸려요.")
        self.run("up", "--detach", "--wait", "--wait-timeout", "120", "postgres", timeout=600)
        return not running

    def stop(self):
        self.run("stop", "--timeout", "30", "postgres", timeout=60)
        print("PostgreSQL 컨테이너를 종료했어요. 저장된 볼륨 데이터는 유지돼요.")

    def backup(self):
        values = dotenv_values(self.root / ".env")
        target = self.root / ".local/backups" / (datetime.now(UTC).strftime("postgres-%Y%m%dT%H%M%S%fZ") + ".dump")
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_suffix(".partial")
        with pending.open("xb") as output:
            result = subprocess.run([*self.command, "exec", "-T", "postgres", "pg_dump", "-U", "postgres",
                                     "--format=custom", "--dbname", values["RESEARCH_DB_NAME"]],
                                    cwd=self.root, stdout=output, stderr=subprocess.PIPE)
        if result.returncode:
            raise LocalDatabaseError("백업을 완료하지 못했어요. PostgreSQL 컨테이너 실행 상태를 확인하세요.")
        pending.rename(target)
        print("백업 저장:", target)


def prepare_environment(root: Path):
    """Generate local passwords once, keeping SQLite active until verified transfer."""
    env_file = root / ".env"
    if not env_file.is_file():
        raise LocalDatabaseError("먼저 scripts/setup.ps1로 .env를 준비하세요.")
    values = dotenv_values(env_file)
    if values.get("RESEARCH_DB_HOST", "localhost") not in {"localhost", "127.0.0.1"}:
        raise LocalDatabaseError("이 도구는 로컬 Docker DB 전용이에요. 팀 DB 설정은 변경하지 않습니다.")
    if values.get("RESEARCH_DB_USERNAME") == "postgres":
        raise LocalDatabaseError("앱 DB 사용자는 postgres 이외의 별도 이름을 지정하세요.")
    backups = root / ".local/backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / "env-before-postgres.env"
    if not backup.exists():
        shutil.copyfile(env_file, backup)
    changes = {"RESEARCH_DB_HOST": "127.0.0.1",
               "RESEARCH_DB_NAME": values.get("RESEARCH_DB_NAME") or "investlab_research",
               "RESEARCH_DB_USERNAME": values.get("RESEARCH_DB_USERNAME") or "investlab_research"}
    for key in ("RESEARCH_DB_PASSWORD", "RESEARCH_DB_ADMIN_PASSWORD"):
        if not values.get(key):
            changes[key] = secrets.token_urlsafe(36)
    for key, value in changes.items():
        set_key(env_file, key, value, quote_mode="always")
    return env_file


def app_ports_busy():
    for port in (8000, 8001, 8002):
        with socket.socket() as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
    return False


def main():
    parser = argparse.ArgumentParser(description="로컬 Docker PostgreSQL 시작/종료/백업")
    parser.add_argument("action", choices=("start", "stop", "backup", "prepare"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare_environment(args.root.resolve())
            print("로컬 PostgreSQL 설정을 준비했어요. 기존 SQLite 데이터는 그대로 유지돼요.")
            return
        load_dotenv(args.root / ".env", override=False)
        db = DockerDatabase(args.root)
        if args.action == "start":
            db.start()
        elif args.action == "stop":
            if app_ports_busy():
                raise LocalDatabaseError("먼저 실행 중인 채팅 서버를 Ctrl+C로 종료하세요.")
            db.stop()
        else:
            db.backup()
    except LocalDatabaseError as exc:
        print(str(exc))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
