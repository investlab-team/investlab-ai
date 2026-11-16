"""Regenerate the provider contract without network or a database connection."""
from pathlib import Path

import yaml

from investlab_ai.config import Settings
from investlab_ai.main import create_app

app = create_app(Settings(service_token="contract-generation-only-token-123456"))
schema = app.openapi()
Path("contracts/internal-ai-api.yaml").write_text(
    yaml.safe_dump(schema, allow_unicode=True, sort_keys=False), encoding="utf-8",
)
