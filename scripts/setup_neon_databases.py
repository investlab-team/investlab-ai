"""Provision separate team databases without exposing passwords or replacing local config.

Run after `neon env pull --file .env.local`. Requires the initial project owner.
The initial admin URI stays in .env.local; apps use their restricted .env.neon.
"""
import base64
import json
import secrets
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def write_env(path: Path, values: dict):
    if path.exists():
        old = dotenv_values(path)
        if any(old.get(key) != value for key, value in values.items()):
            raise RuntimeError(f"Refusing to replace existing configuration: {path.name}")
        return
    path.write_text("".join(f'{key}={json.dumps(value)}\n' for key, value in values.items()),
                    encoding="utf-8")


def main():
    values = dotenv_values(ROOT / ".env.local")
    direct = make_url(values["DATABASE_URL_UNPOOLED"])
    pooled = make_url(values["DATABASE_URL"])
    if "-pooler." in direct.host:
        raise RuntimeError("A direct connection is required for database provisioning")
    secret_file = ROOT / ".local/neon-role-secrets.json"
    if secret_file.exists():
        credentials = json.loads(secret_file.read_text())
    else:
        credentials = {name: secrets.token_urlsafe(36)
                       for name in ("investlab_core", "investlab_research")}
        secret_file.write_text(json.dumps(credentials), encoding="utf-8")
    with psycopg.connect(direct.render_as_string(hide_password=False), autocommit=True) as conn:
        admin = conn.execute("SELECT current_user").fetchone()[0]
        for name, password in credentials.items():
            role_exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
            if not role_exists:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}").format(
                    sql.Identifier(name), sql.Literal(password)))
            exists = conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)).fetchone()
            if not exists:
                # Neon SQL-created app roles are not neon_superuser members.
                conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(name), sql.Identifier(admin)))
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(
                    sql.Identifier(name), sql.Identifier(name)))
            elif exists[0] != name:
                raise RuntimeError("Existing database owner differs; refusing to modify ownership")
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(name)))
            conn.execute(sql.SQL("ALTER ROLE {} SET timezone='UTC'").format(sql.Identifier(name)))
            url = direct.set(database=name, username=name, password=password)
            with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as app:
                app.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            print(name, "database and dedicated role ready")
    research = "investlab_research"
    write_env(ROOT / ".env.neon", {
        "RESEARCH_DB_URL": pooled.set(database=research, username=research,
                                     password=credentials[research]).render_as_string(hide_password=False),
        "RESEARCH_DB_DIRECT_URL": direct.set(database=research, username=research,
                                            password=credentials[research]).render_as_string(hide_password=False),
        "INVESTLAB_AI_OBJECT_BUCKET": "disclosures",
    })
    backend = ROOT.parent / "investlab-backend"
    if backend.is_dir():
        core = "investlab_core"
        existing = dotenv_values(backend / ".env.neon")
        write_env(backend / ".env.neon", {
            "SPRING_PROFILES_ACTIVE": "postgres", "DB_USERNAME": core,
            "DB_PASSWORD": credentials[core],
            "DB_URL": f"jdbc:postgresql://{pooled.host}:{pooled.port or 5432}/{core}?sslmode=require",
            "FLYWAY_URL": f"jdbc:postgresql://{direct.host}:{direct.port or 5432}/{core}?sslmode=require",
            "FLYWAY_USER": core, "FLYWAY_PASSWORD": credentials[core],
            "JWT_SECRET_BASE64": existing.get("JWT_SECRET_BASE64") or base64.b64encode(
                secrets.token_bytes(64)).decode(),
        })
    print("Private .env.neon files written; no existing .env was changed")


if __name__ == "__main__":
    main()
