import io
import os
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(".local/tiktoken").resolve()))


@pytest.fixture(autouse=True)
def isolated_private_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTLAB_AI_PRIVATE_CACHE_DIR", str(tmp_path / "private-cache"))


@pytest.fixture
def archive():
    def make(text: str, filename: str = "report.xml") -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as file:
            file.writestr(filename, text.encode("utf-8"))
        return output.getvalue()
    return make
