"""Copy local research evidence to the shared Neon database + private objects.

Source is a PostgreSQL READ ONLY repeatable-read snapshot. No source writes,
no DART or OpenAI calls. Reruns preserve equivalent target records and refuse
conflicts. Each uploaded object is read back and SHA-256 checked before commit.
Runtime chat jobs and transient analysis locks are intentionally not copied.
"""
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import make_url

from investlab_ai.object_storage import EvidenceObjects

ROOT = Path(__file__).resolve().parents[1]
DOC_FIELDS = ("rcept_no", "document_id", "disclosure", "content_hash", "embedding_model",
              "embedding_dimensions", "parser_version", "collected_at", "chunk_count", "warnings")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    local = dotenv_values(ROOT / ".env")
    source_url = URL.create("postgresql+psycopg", host=local["RESEARCH_DB_HOST"],
                            port=int(local.get("RESEARCH_DB_PORT", 5432)),
                            username=local["RESEARCH_DB_USERNAME"], password=local["RESEARCH_DB_PASSWORD"],
                            database=local.get("RESEARCH_DB_NAME", "investlab_research"))
    neon = dotenv_values(ROOT / ".env.neon")
    target_url = make_url(neon["RESEARCH_DB_DIRECT_URL"]).set(drivername="postgresql+psycopg")
    if source_url.host == target_url.host:
        raise RuntimeError("SOURCE_EQUALS_TARGET")
    load_dotenv(ROOT / ".env.local", override=True)
    objects = EvidenceObjects(neon["INVESTLAB_AI_OBJECT_BUCKET"])
    source_engine = create_engine(source_url, pool_size=args.workers + 1,
                                  connect_args={"connect_timeout": 15})
    target_engine = create_engine(target_url, pool_size=args.workers + 1, pool_pre_ping=True,
                                  connect_args={"connect_timeout": 15})
    report = {"startedAt": datetime.now(UTC).isoformat(), "sourceUnchanged": True,
              "openaiCalls": 0, "objectsVerified": 0, "objectBytes": 0}
    with source_engine.connect().execution_options(isolation_level="REPEATABLE READ") as source:
        source.exec_driver_sql("SET TRANSACTION READ ONLY")
        snapshot = source.scalar(text("SELECT pg_export_snapshot()"))
        receipts = list(source.execute(text("SELECT rcept_no FROM rag_documents ORDER BY rcept_no")).scalars())
        expected_analyses = source.scalar(text("SELECT count(*) FROM rag_analyses"))
        company_count = source.scalar(text("SELECT count(*) FROM rag_companies"))
        source_chunks = source.scalar(text("SELECT count(*) FROM rag_chunks"))
        with target_engine.begin() as target:
            count = target.scalar(text("SELECT count(*) FROM rag_companies"))
            if count not in (0, company_count):
                raise RuntimeError("TARGET_COMPANY_CATALOG_DIFFERS")
            if not count:
                with target.connection.driver_connection.cursor() as cursor:
                    with cursor.copy("COPY rag_companies(corp_code,data) FROM STDIN") as copy:
                        for code, data in source.execute(text("SELECT corp_code,data FROM rag_companies ORDER BY corp_code")):
                            copy.write_row((code, Jsonb(data)))
        # Verify every catalog row, including all JSON fields, without a million round trips.
        with target_engine.connect() as target:
            actual = dict(target.execute(text("SELECT corp_code,data FROM rag_companies")).all())
        if any(actual.get(code) != data for code, data in source.execute(text("SELECT corp_code,data FROM rag_companies"))):
            raise RuntimeError("COMPANY_CATALOG_VERIFICATION_FAILED")
        print(f"Verified company catalog: {company_count}", flush=True)

        def migrate_one(receipt):
            with source_engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql("SET TRANSACTION SNAPSHOT '" + snapshot.replace("'", "''") + "'")
                row = dict(conn.execute(text("SELECT " + ",".join(DOC_FIELDS)
                                             + ",original_zip FROM rag_documents WHERE rcept_no=:r"),
                                        {"r": receipt}).mappings().one())
                original = bytes(row.pop("original_zip"))
                records = [dict(item) for item in conn.execute(text(
                    "SELECT chunk_id,ordinal,section,text,token_count,embedding FROM rag_chunks "
                    "WHERE rcept_no=:r ORDER BY ordinal"), {"r": receipt}).mappings()]
                analyses = [dict(item) for item in conn.execute(text(
                    "SELECT cache_key,rcept_no,result,created_at FROM rag_analyses WHERE rcept_no=:r"),
                    {"r": receipt}).mappings()]
            if len(records) != row["chunk_count"] or hashlib.sha256(original).hexdigest() != row["content_hash"]:
                raise RuntimeError("SOURCE_EVIDENCE_INCONSISTENT:" + receipt)
            encoded = objects.encode_chunks(receipt, records)
            with target_engine.connect() as target:
                existing = target.execute(text("SELECT content_hash,original_object_key,chunks_object_key "
                                                "FROM rag_documents WHERE rcept_no=:r"), {"r": receipt}).mappings().first()
            original_key = f"dart/{receipt}/v1/original.zip/{hashlib.sha256(original).hexdigest()}"
            chunks_key = f"dart/{receipt}/v1/chunks.json.gz/{hashlib.sha256(encoded).hexdigest()}"
            if existing:
                if (existing["content_hash"] != row["content_hash"]
                        or existing["original_object_key"] != original_key
                        or existing["chunks_object_key"] != chunks_key):
                    raise RuntimeError("TARGET_DOCUMENT_DIFFERS:" + receipt)
                if objects.get(original_key) != original or objects.get(chunks_key) != encoded:
                    raise RuntimeError("TARGET_OBJECT_DIFFERS:" + receipt)
            else:
                original_key = objects.put(receipt, "original.zip", original, verify=True)
                chunks_key = objects.put(receipt, "chunks.json.gz", encoded, verify=True)
            with target_engine.begin() as target:
                if not existing:
                    data = {**row, "original_zip": None, "original_object_key": original_key,
                            "chunks_object_key": chunks_key}
                    cols = list(data)
                    # Typed JSON adaptation for both receipt metadata and cached analyses.
                    with target.connection.driver_connection.cursor() as cursor:
                        cursor.execute(sql.SQL("INSERT INTO rag_documents ({}) VALUES ({})").format(
                            sql.SQL(",").join(map(sql.Identifier, cols)),
                            sql.SQL(",").join(sql.Placeholder() for _ in cols)),
                            [Jsonb(data[k]) if k in ("disclosure", "warnings") else data[k] for k in cols])
                for analysis in analyses:
                    previous = target.execute(text("SELECT result FROM rag_analyses WHERE cache_key=:k"),
                                              {"k": analysis["cache_key"]}).scalar_one_or_none()
                    if previous is not None:
                        if previous != analysis["result"]:
                            raise RuntimeError("TARGET_ANALYSIS_DIFFERS:" + receipt)
                    else:
                        with target.connection.driver_connection.cursor() as cursor:
                            cursor.execute("INSERT INTO rag_analyses(cache_key,rcept_no,result,created_at) VALUES(%s,%s,%s,%s)",
                                           (analysis["cache_key"], receipt, Jsonb(analysis["result"]), analysis["created_at"]))
            return len(original) + len(encoded), len(records), len(analyses)

        report.update(companies=company_count, documents=0, chunks=0, analyses=0)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = {pool.submit(migrate_one, receipt): receipt for receipt in receipts}
            for future in as_completed(pending):
                size, chunks, analyses = future.result()
                report["documents"] += 1
                report["chunks"] += chunks
                report["analyses"] += analyses
                report["objectsVerified"] += 2
                report["objectBytes"] += size
                if report["documents"] % 25 == 0 or report["documents"] == len(receipts):
                    print(f"Verified {report['documents']}/{len(receipts)} reports; {report['analyses']} analyses", flush=True)
                    (ROOT / ".local/neon-transfer-progress.json").write_text(json.dumps(report), encoding="utf-8")
        if report["chunks"] != source_chunks or report["analyses"] != expected_analyses:
            raise RuntimeError("FINAL_SOURCE_COUNTS_DIFFER")
    with target_engine.connect() as target:
        counts = target.execute(text("SELECT (SELECT count(*) FROM rag_documents), "
                                     "(SELECT count(*) FROM rag_analyses), (SELECT count(*) FROM disclosure_documents), "
                                     "pg_database_size(current_database())")).one()
        if tuple(counts[:3]) != (len(receipts), expected_analyses, len(receipts)):
            raise RuntimeError("FINAL_TARGET_COUNTS_DIFFER")
        report["databaseBytes"] = counts[3]
        report["analyzedCompanies"] = target.scalar(text(
            "SELECT count(DISTINCT d.disclosure->>'corp_code') FROM rag_documents d JOIN rag_analyses a USING(rcept_no)"))
    report["finishedAt"] = datetime.now(UTC).isoformat()
    output = ROOT.parent / "investlab-neon-transfer-status.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Verified transfer complete; local PostgreSQL source preserved", flush=True)
    source_engine.dispose()
    target_engine.dispose()


if __name__ == "__main__":
    main()
