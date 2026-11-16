"""Explicit Neon profile; keep the existing local PostgreSQL configuration intact."""
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from investlab_ai.config import DatabaseSettings
from investlab_ai.errors import ServiceError


def load_neon_environment(root: Path) -> None:
    path = root / ".env.local"
    if not path.is_file():
        raise ServiceError("NEON_ENV_FILE_MISSING", 503)
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / ".env.toss", override=True)
    load_dotenv(root / ".env.sec", override=True)
    profile = root / ".env.neon"
    if profile.is_file():
        # Keep project-owner database credentials out of the AI server process.
        for key, value in dotenv_values(path).items():
            if value is not None and (key.startswith("AWS_") or key.startswith("NEON_")):
                os.environ[key] = value
        load_dotenv(profile, override=True)
    else:
        load_dotenv(path, override=True)
    if not DatabaseSettings().connection_url:
        raise ServiceError("NEON_DATABASE_URL_MISSING", 503)
    os.environ.pop("INVESTLAB_AI_LOCAL_SQLITE_PATH", None)
    os.environ["INVESTLAB_AI_USE_DOCKER_DB"] = "false"
    os.environ["INVESTLAB_AI_ENABLE_LOCAL_CHAT"] = "true"
