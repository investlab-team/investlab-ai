from pathlib import Path

import yaml

from investlab_ai.config import Settings
from investlab_ai.main import create_app


def test_checked_in_contract_matches_api():
    app = create_app(Settings(service_token="contract-generation-only-token-123456"))
    stored = yaml.safe_load(Path("contracts/internal-ai-api.yaml").read_text(encoding="utf-8"))
    assert stored == app.openapi()
    for path, operations in stored["paths"].items():
        if path.startswith("/internal/"):
            assert all(op.get("security") for op in operations.values())
