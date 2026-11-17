"""Build shareable app + seeded DB images. Never publishes or includes .env."""
import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select, text

from investlab_ai.config import Settings
from investlab_ai.local_database import DockerDatabase
from investlab_ai.storage import AnalysisRow, Base, ChunkRow, CompanyRow, DocumentRow, Repository

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_TABLES = ("rag_chat_tasks", "rag_analysis_leases", "rag_bulk_runs", "rag_bulk_items")


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def build(docker, image, dockerfile, context, log):
    print("Building " + image, flush=True)
    with log.open("wb") as output:
        result = subprocess.run([docker, "build", "--platform", "linux/amd64", "--tag", image,
                                 "--file", str(dockerfile), str(context)],
                                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError("Image build failed; inspect " + str(log))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=datetime.now(UTC).strftime("%Y%m%d"))
    parser.add_argument("--output", type=Path, default=ROOT.parent / "investlab-docker-bundle")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", args.tag):
        raise SystemExit("Invalid image tag")
    bundle = args.output.resolve()
    bundle.mkdir(parents=True, exist_ok=True)
    if (bundle / "images.tar.gz").exists():
        raise SystemExit("Use a new output directory for a new release")
    load_dotenv(ROOT / ".env", override=True)
    settings = Settings()
    if settings.local_sqlite_path is not None:
        raise SystemExit("Export requires the migrated PostgreSQL database")
    db = DockerDatabase(ROOT)
    started = db.start()
    repo = Repository(settings)
    stage = ROOT / ".local/distribution-build" / args.tag
    stage.mkdir(parents=True, exist_ok=True)
    (stage / ".dockerignore").write_text(
        "*\n!seed.dump\n!seed.json\n!seed.sha256\n!01-app-user.sh\n!02-restore-seed.sh\n", encoding="ascii")
    try:
        manifest = {"createdAt": datetime.now(UTC).isoformat(), "platform": "linux/amd64",
                    "model": settings.openai_model, "basicAnalysisTokens": settings.basic_analysis_tokens,
                    "excludedDataTables": list(PRIVATE_TABLES)}
        # Exported snapshot keeps counts, schema and copied data consistent while others work.
        with repo.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            snapshot = connection.scalar(text("select pg_export_snapshot()"))
            counts = {name: connection.scalar(select(func.count()).select_from(table)) for name, table in (
                ("documents", DocumentRow), ("analyses", AnalysisRow),
                ("chunks", ChunkRow), ("companyCatalog", CompanyRow))}
            counts["analyzedCompanies"] = connection.scalar(select(func.count(func.distinct(
                DocumentRow.disclosure["corp_code"].as_string()))).join(
                    AnalysisRow, AnalysisRow.rcept_no == DocumentRow.rcept_no).where(
                        AnalysisRow.result["analysis_scope"].as_string() == "basic"))
            manifest["counts"] = counts
            print("Exporting public filing data and saved analyses: " + json.dumps(counts), flush=True)
            tables = ["alembic_version", *(table.name for table in Base.metadata.sorted_tables)]
            command = [*db.command, "exec", "-T", "postgres", "pg_dump", "-U", "postgres",
                       "--dbname", "investlab_research", "--format=custom", "--no-owner", "--no-acl",
                       "--snapshot", snapshot, *("--table=public." + name for name in tables),
                       *("--exclude-table-data=public." + name for name in PRIVATE_TABLES)]
            with (stage / "seed.dump").open("wb") as output:
                result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError("Database snapshot export failed")
            connection.rollback()
        manifest["dumpSha256"] = digest(stage / "seed.dump")
        (stage / "seed.sha256").write_text(manifest["dumpSha256"] + "  seed.dump\n", encoding="ascii")
        (stage / "seed.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for source, name in ((ROOT / "docker/postgres/01-app-user.sh", "01-app-user.sh"),
                             (ROOT / "docker/distribution/02-restore-seed.sh", "02-restore-seed.sh")):
            (stage / name).write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
        app_image, database_image = "investlab-ai:" + args.tag, "investlab-ai-db:" + args.tag
        docker = db.command[0]
        build(docker, app_image, ROOT / "Dockerfile", ROOT, stage / "build-app.log")
        build(docker, database_image, ROOT / "docker/distribution/Dockerfile.database", stage,
              stage / "build-database.log")
        images = {"app": app_image, "database": database_image, **manifest}
        images["imageIds"] = {image: subprocess.check_output(
            [docker, "image", "inspect", "--format", "{{.Id}}", image], text=True).strip()
            for image in (app_image, database_image)}
        for filename in ("compose.yaml", "start.ps1", "start.cmd", "stop.cmd", "start.sh", "README.txt", ".gitignore"):
            shutil.copyfile(ROOT / "docker/distribution" / filename, bundle / filename)
        (bundle / "images.json").write_text(json.dumps(images, ensure_ascii=False, indent=2), encoding="utf-8")
        env = (f"INVESTLAB_APP_IMAGE={app_image}\nINVESTLAB_DB_IMAGE={database_image}\n"
               f"INVESTLAB_AI_OPENAI_MODEL={settings.openai_model}\n"
               f"INVESTLAB_AI_BASIC_ANALYSIS_TOKENS={settings.basic_analysis_tokens}\n"
               "RESEARCH_DB_ADMIN_PASSWORD=CHANGE_ME_ADMIN\nRESEARCH_DB_PASSWORD=CHANGE_ME_DATABASE\n"
               "INVESTLAB_AI_SERVICE_TOKEN=CHANGE_ME_SERVICE\nINVESTLAB_CHAT_PORT=8000\n"
               "INVESTLAB_AI_DART_API_KEY=\nINVESTLAB_AI_OPENAI_API_KEY=\n")
        (bundle / ".env.example").write_text(env, encoding="utf-8")
        print("Exporting image archive for docker load. No registry upload.", flush=True)
        pending = bundle / "images.tar.gz.partial"
        with (stage / "save.log").open("wb") as errors:
            process = subprocess.Popen([docker, "image", "save", app_image, database_image],
                                       stdout=subprocess.PIPE, stderr=errors)
            with gzip.open(pending, "wb", compresslevel=1) as archive:
                shutil.copyfileobj(process.stdout, archive, length=1024 * 1024)
            if process.wait():
                raise RuntimeError("Image export failed")
        archive = bundle / "images.tar.gz"
        pending.rename(archive)
        (bundle / "images.sha256").write_text(digest(archive) + "  images.tar.gz\n", encoding="ascii")
        print("Bundle ready: " + str(bundle), flush=True)
    finally:
        repo.close()
        if started:
            db.stop()


if __name__ == "__main__":
    main()
