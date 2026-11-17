"""Deduplicate basic analysis generation across local/bulk processes."""
import sqlalchemy as sa
from alembic import op

revision = "0003_analysis_leases"
down_revision = "0002_chat_and_cache"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rag_analysis_leases",
                    sa.Column("cache_key", sa.String(64), primary_key=True),
                    sa.Column("owner", sa.String(36), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("rag_analysis_leases")
