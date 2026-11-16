"""Private, immutable evidence objects; PostgreSQL remains the metadata authority."""
import gzip
import hashlib
import io
import json

import boto3
from botocore.config import Config

from investlab_ai.errors import ServiceError

MAX_OBJECT_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


class EvidenceObjects:
    def __init__(self, bucket: str):
        self.bucket = bucket
        self.client = boto3.client("s3", config=Config(
            signature_version="s3v4", s3={"addressing_style": "path"},
            retries={"max_attempts": 4, "mode": "standard"},
            connect_timeout=15, read_timeout=90,
        ))

    def put(self, receipt: str, kind: str, data: bytes, *, verify: bool = False,
            namespace: str = "dart") -> str:
        if len(data) > MAX_OBJECT_BYTES:
            raise ServiceError("EVIDENCE_OBJECT_TOO_LARGE", 413)
        digest = hashlib.sha256(data).hexdigest()
        if namespace not in {"dart", "sec"}:
            raise ValueError("Invalid evidence namespace")
        key = f"{namespace}/{receipt}/v1/{kind}/{digest}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType="application/octet-stream",
                               Metadata={"sha256": digest})
        if verify and self.get(key) != data:
            raise ServiceError("EVIDENCE_OBJECT_CORRUPT", 503)
        return key

    def get(self, key: str) -> bytes:
        result = self.client.get_object(Bucket=self.bucket, Key=key)
        with result["Body"] as stream:
            data = stream.read(MAX_OBJECT_BYTES + 1)
        if (len(data) > MAX_OBJECT_BYTES
                or hashlib.sha256(data).hexdigest() != key.rsplit("/", 1)[-1]):
            raise ServiceError("EVIDENCE_OBJECT_CORRUPT", 503)
        return data

    @staticmethod
    def encode_chunks(receipt: str, records: list[dict]) -> bytes:
        data = json.dumps({"version": 1, "receipt": receipt, "chunks": records},
                          ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ServiceError("EVIDENCE_OBJECT_TOO_LARGE", 413)
        return gzip.compress(data, compresslevel=5, mtime=0)

    @staticmethod
    def decode_chunks(data: bytes, receipt: str, count: int) -> list[dict]:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                raw = stream.read(MAX_ARCHIVE_BYTES + 1)
            if len(raw) > MAX_ARCHIVE_BYTES:
                raise ValueError("archive size")
            archive = json.loads(raw)
            records = archive["chunks"]
            if archive["version"] != 1 or archive["receipt"] != receipt or len(records) != count:
                raise ValueError("archive identity")
            if any(row["ordinal"] != i or not isinstance(row["embedding"], list)
                   or not isinstance(row["text"], str) for i, row in enumerate(records)):
                raise ValueError("archive records")
            return records
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise ServiceError("EVIDENCE_OBJECT_CORRUPT", 503) from exc

    def chunks(self, key: str, receipt: str, count: int) -> list[dict]:
        return self.decode_chunks(self.get(key), receipt, count)
