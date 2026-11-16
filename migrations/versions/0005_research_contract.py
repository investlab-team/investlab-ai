"""Team ERD v1.1 research tables and a transactional adapter for the working RAG store."""
from pathlib import Path

from alembic import op

revision = "0005_research_contract"
down_revision = "0004_evidence_objects"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    sql = Path(__file__).with_name("research_contract.sql").read_text(encoding="utf-8")
    # No DB-API parameters: SQL contains LIKE patterns and PL/pgSQL bodies.
    with op.get_bind().connection.driver_connection.cursor() as cursor:
        cursor.execute(sql, prepare=False)


def downgrade():
    raise RuntimeError("Research contract downgrade requires an explicit data-preserving migration")
