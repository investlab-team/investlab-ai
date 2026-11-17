import hashlib
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select

from investlab_ai.database_transfer import TransferError, transfer
from investlab_ai.storage import (
    AnalysisLeaseRow,
    AnalysisRow,
    Base,
    ChunkRow,
    CompanyRow,
    DocumentRow,
)


@pytest.fixture
def databases(tmp_path):
    engines = [create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
               for name in ("source.db", "target.db")]
    for engine in engines:
        Base.metadata.create_all(engine)
    raw = b"PK\x00original-disclosure-archive\xff"
    receipt = "20261113000632"
    now = datetime.now(UTC)
    with engines[0].begin() as source:
        source.execute(CompanyRow.__table__.insert(), [{"corp_code": "01027794", "data": {
            "corp_code": "01027794", "corp_name": "브이원텍", "stock_code": "251630"}}])
        source.execute(DocumentRow.__table__.insert(), [{
            "rcept_no": receipt, "document_id": "document-1", "disclosure": {
                "corp_code": "01027794", "report_name": "분기보고서 (2026.09)"},
            "content_hash": hashlib.sha256(raw).hexdigest(), "original_zip": raw,
            "embedding_model": "test-model", "embedding_dimensions": 2,
            "parser_version": "test", "collected_at": now, "chunk_count": 1,
            "warnings": ["보존된 원문"],
        }])
        source.execute(ChunkRow.__table__.insert(), [{
            "chunk_id": "source-1", "rcept_no": receipt, "ordinal": 0, "section": "사업 / 재무",
            "text": "매출 100억원\n공시 원문입니다.", "token_count": 20, "embedding": [0.125, -0.5],
        }])
        source.execute(AnalysisRow.__table__.insert(), [{
            "cache_key": "a" * 64, "rcept_no": receipt, "created_at": now,
            "result": {"analysis_scope": "basic", "claims": [{"text": "분석 결과",
                                                            "evidence_ids": ["source-1"]}]},
        }])
        source.execute(AnalysisLeaseRow.__table__.insert(), [{
            "cache_key": "b" * 64, "owner": "old-worker", "expires_at": now,
        }])
    yield engines
    for engine in engines:
        engine.dispose()


def test_transfer_preserves_original_json_vectors_and_cache_then_can_resume(databases):
    source, target = databases
    report = transfer(source, target, batch_size=1)
    assert report["tables"]["rag_documents"]["rows"] == 1
    assert report["tables"]["rag_chunks"]["rows"] == 1
    assert "rag_analysis_leases" not in report["tables"]
    with target.connect() as connection:
        assert connection.scalar(select(AnalysisRow.result))["claims"][0]["text"] == "분석 결과"
        assert connection.scalar(select(ChunkRow.embedding)) == [0.125, -0.5]
        assert connection.scalar(select(DocumentRow.original_zip)) == b"PK\x00original-disclosure-archive\xff"
        assert connection.scalar(select(func.count()).select_from(AnalysisLeaseRow)) == 0
    again = transfer(source, target)
    assert all(item["reused"] for item in again["tables"].values() if item["rows"])
    assert again["tables"]["rag_analyses"]["sha256"] == report["tables"]["rag_analyses"]["sha256"]


def test_transfer_refuses_to_overwrite_different_existing_data(databases):
    source, target = databases
    transfer(source, target)
    with target.begin() as connection:
        connection.execute(CompanyRow.__table__.update().values(data={"corp_name": "다른 자료"}))
    with pytest.raises(TransferError, match="TARGET_TABLE_DIFFERS:rag_companies"):
        transfer(source, target)
    with target.connect() as connection:
        assert connection.scalar(select(CompanyRow.data)) == {"corp_name": "다른 자료"}


def test_same_database_is_never_a_transfer_target(databases):
    with pytest.raises(TransferError, match="SOURCE_EQUALS_TARGET"):
        transfer(databases[0], databases[0])


def test_alembic_migrates_explicit_connection_without_sqlite_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("INVESTLAB_AI_SERVICE_TOKEN", raising=False)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'schema.db'}")
    config = Config("alembic.ini")
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        assert connection.exec_driver_sql("select version_num from alembic_version").scalar() == "0006_sec_research"
        assert connection.scalar(select(func.count()).select_from(DocumentRow)) == 0
    engine.dispose()
