"""Bounded local cache; brokerage credentials and market data stay outside shared DB."""
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout


class PrivateCache:
    def __init__(self, root: Path, namespace: str, max_entries: int = 256):
        # Keep Windows paths below MAX_PATH even under long test/workspace roots.
        self.root = root / hashlib.sha256(namespace.encode()).hexdigest()[:24]
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries

    def path(self, key: str) -> Path:
        return self.root / (hashlib.sha256(key.encode()).hexdigest()[:40] + ".json")

    def lock(self, key: str) -> FileLock:
        return FileLock(str(self.path(key)) + ".lock", timeout=10)

    def get(self, key: str):
        try:
            entry = json.loads(self.path(key).read_text(encoding="utf-8"))
            return entry["value"] if entry["expiresAt"] > time.time() else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, key: str, value, seconds: float) -> None:
        target = self.path(key)
        temporary = self.root / (uuid4().hex[:16] + ".tmp")
        payload = json.dumps({"expiresAt": time.time() + seconds, "value": value},
                             ensure_ascii=False, allow_nan=False)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(payload)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        # Bounded retention; lock files are tiny and contain no credentials or market values.
        try:
            with self.lock("eviction").acquire(timeout=0):
                files = []
                for path in self.root.glob("*.json"):
                    try:
                        files.append((path.stat().st_mtime, path))
                    except FileNotFoundError:
                        continue
                for _, path in sorted(files)[:max(0, len(files) - self.max_entries)]:
                    path.unlink(missing_ok=True)
        except Timeout:
            pass
