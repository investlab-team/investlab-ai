from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.parser import ParsedChunk
from investlab_ai.object_storage import EvidenceObjects
from investlab_ai.schemas import Disclosure
from investlab_ai.storage import ChunkRow, DocumentRow, Repository


@dataclass
class MemoryObjects:
    data: dict
    encode_chunks = staticmethod(EvidenceObjects.encode_chunks)

    def put(self, receipt, kind, data):
        key = f"{receipt}/{kind}/{len(self.data)}"
        self.data[key] = data
        return key

    def get(self, key):
        return self.data[key]

    def chunks(self, key, receipt, count):
        return EvidenceObjects.decode_chunks(self.get(key), receipt, count)


def test_external_evidence_survives_reconnect_and_reindex(tmp_path):
    settings = Settings(service_token="test-object-storage-token-1234567890",
                        local_sqlite_path=tmp_path / "db.sqlite")
    repo = Repository(settings)
    repo.initialize_local()
    objects = MemoryObjects({})
    repo.objects = objects
    disclosure = Disclosure(rcept_no="20261115000001", corp_code="00126380",
                            corp_name="예시", report_name="분기보고서", filed_on=date(2026, 11, 15),
                            source_url="https://dart.fss.or.kr/")
    chunks = [ParsedChunk(chunk_id="a" * 32, section="매출", text="매출 100억원", token_count=8)]
    repo.save_document(disclosure, b"zip", "b" * 64, "old", chunks, [[0.1, 0.2]], [])
    with Session(repo.engine) as session:
        row = session.get(DocumentRow, disclosure.rcept_no)
        assert row.original_zip is None and row.original_object_key and row.chunks_object_key
        assert list(session.scalars(select(ChunkRow))) == []
    repo.close()
    repo = Repository(settings)
    repo.objects = objects
    assert repo.original(disclosure.rcept_no) == b"zip"
    assert repo.chunks(disclosure.rcept_no)[0][0].text == "매출 100억원"
    repo.index_document(disclosure.rcept_no, "new", [[0.3, 0.4]])
    assert repo.chunks(disclosure.rcept_no)[0][1] == [0.3, 0.4]
    assert repo.document(disclosure.rcept_no).embedding_model == "new"
    repo.close()


def test_archive_rejects_other_document_or_incomplete_chunk_list():
    raw = EvidenceObjects.encode_chunks("receipt-one", [])
    assert EvidenceObjects.decode_chunks(raw, "receipt-one", 0) == []
    for receipt, count in (("receipt-two", 0), ("receipt-one", 1)):
        with pytest.raises(ServiceError, match="EVIDENCE_OBJECT_CORRUPT"):
            EvidenceObjects.decode_chunks(raw, receipt, count)
