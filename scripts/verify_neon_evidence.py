"""A tiny synthetic live write/read/reindex check; always removes its own fixtures.

Never calls DART/OpenAI. Uses an impossible real DART year (0000) plus random suffix.
"""
import hashlib
import secrets
from datetime import date
from pathlib import Path

from sqlalchemy import text

from investlab_ai.config import Settings
from investlab_ai.ingestion.parser import ParsedChunk
from investlab_ai.neon_environment import load_neon_environment
from investlab_ai.schemas import Disclosure
from investlab_ai.storage import Repository


def main():
    load_neon_environment(Path(__file__).resolve().parents[1])
    repo = Repository(Settings())
    receipt = f"0000{secrets.randbelow(10**10):010}"
    prefix = f"dart/{receipt}/"
    original = b"investlab-live-evidence-storage-check-v1"
    digest = hashlib.sha256(original).hexdigest()
    if repo.document(receipt) or repo.objects.client.list_objects_v2(
            Bucket=repo.objects.bucket, Prefix=prefix).get("Contents"):
        raise RuntimeError("Fixture already exists; refusing to touch it")
    try:
        company = repo.company("00126380")
        assert company is not None
        disclosure = Disclosure(rcept_no=receipt, corp_code=company.corp_code,
                                corp_name=company.corp_name, report_name="Storage setup verification",
                                filed_on=date.today(), source_url="https://dart.fss.or.kr/")
        chunk = ParsedChunk(chunk_id=hashlib.sha256(receipt.encode()).hexdigest()[:32],
                            section="Storage verification", text="Synthetic verification evidence",
                            token_count=8)
        repo.save_document(disclosure, original, digest, "setup-check-only", [chunk], [[1.0, 0.2]], [])
        assert repo.original(receipt) == original
        assert repo.chunks(receipt)[0][0].text == chunk.text
        repo.index_document(receipt, "setup-check-only-v2", [[0.2, 1.0]])
        assert repo.chunks(receipt)[0][1] == [0.2, 1.0]
        with repo.engine.connect() as conn:
            assert conn.execute(text("SELECT raw_object_key FROM disclosure_documents WHERE external_id=:r"),
                                {"r": receipt}).scalar_one().startswith(prefix)
        print("Live shared evidence save/read/reindex + ERD adapter: PASS (OpenAI calls: 0)")
    finally:
        with repo.engine.begin() as conn:
            row = conn.execute(text("SELECT content_hash,document_id FROM rag_documents WHERE rcept_no=:r"),
                               {"r": receipt}).first()
            if row:
                if row[0] != digest:
                    raise RuntimeError("Fixture identity changed; refusing cleanup")
                conn.execute(text("DELETE FROM disclosure_documents WHERE external_id=:r AND provider='DART'"),
                             {"r": receipt})
                conn.execute(text("DELETE FROM rag_documents WHERE rcept_no=:r"), {"r": receipt})
        page = repo.objects.client.list_objects_v2(Bucket=repo.objects.bucket, Prefix=prefix)
        for item in page.get("Contents", []):
            repo.objects.client.delete_object(Bucket=repo.objects.bucket, Key=item["Key"])
        repo.close()
        print("Synthetic DB records and objects removed")


if __name__ == "__main__":
    main()
