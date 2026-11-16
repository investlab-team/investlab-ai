"""US SEC identities, public filing objects, cached analyses and resumable runs."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_sec_research"
down_revision = "0005_research_contract"
branch_labels = None
depends_on = None


def upgrade():
    data = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("rag_sec_issuers", sa.Column("cik", sa.String(10), primary_key=True),
                    sa.Column("data", data, nullable=False))
    op.create_table("rag_sec_filings",
                    sa.Column("accession", sa.String(20), primary_key=True),
                    sa.Column("cik", sa.String(10), sa.ForeignKey("rag_sec_issuers.cik"), nullable=False),
                    sa.Column("data", data, nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
                    sa.Column("original_key", sa.Text(), nullable=False), sa.Column("chunks_key", sa.Text(), nullable=False),
                    sa.Column("parser_version", sa.String(40), nullable=False), sa.Column("chunk_count", sa.Integer(), nullable=False),
                    sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_rag_sec_filings_cik", "rag_sec_filings", ["cik"])
    op.create_table("rag_sec_analyses", sa.Column("cache_key", sa.String(64), primary_key=True),
                    sa.Column("accession", sa.String(20), sa.ForeignKey("rag_sec_filings.accession"), nullable=False),
                    sa.Column("result", data, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_rag_sec_analyses_accession", "rag_sec_analyses", ["accession"])
    op.create_table("rag_sec_runs", sa.Column("run_id", sa.String(36), primary_key=True),
                    sa.Column("data", data, nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("rag_sec_items", sa.Column("run_id", sa.String(36), sa.ForeignKey("rag_sec_runs.run_id"), primary_key=True),
                    sa.Column("accession", sa.String(20), primary_key=True), sa.Column("data", data, nullable=False),
                    sa.Column("status", sa.String(24), nullable=False), sa.Column("error_code", sa.String(80), nullable=True))


def downgrade():
    raise RuntimeError("SEC data downgrade requires an explicit data-preserving migration")
