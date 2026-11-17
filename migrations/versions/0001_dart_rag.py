"""Isolated DART RAG tables; does not alter existing research/core tables."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_dart_rag"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "rag_companies",
        sa.Column("corp_code", sa.String(8), primary_key=True),
        sa.Column("data", json_type, nullable=False),
    )
    op.create_table(
        "rag_documents",
        sa.Column("rcept_no", sa.String(14), primary_key=True),
        sa.Column("document_id", sa.String(36), unique=True, nullable=False),
        sa.Column("disclosure", json_type, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("original_zip", sa.LargeBinary(), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("warnings", json_type, nullable=False),
    )
    op.create_table(
        "rag_chunks",
        sa.Column("chunk_id", sa.String(32), primary_key=True),
        sa.Column("rcept_no", sa.String(14), sa.ForeignKey("rag_documents.rcept_no"),
                  nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", json_type, nullable=False),
    )
    op.create_index("ix_rag_chunks_rcept_no", "rag_chunks", ["rcept_no"])


def downgrade():
    op.drop_table("rag_chunks")
    op.drop_table("rag_documents")
    op.drop_table("rag_companies")
