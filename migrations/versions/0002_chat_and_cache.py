"""Persistent shared analyses and local chat/bulk progress."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_chat_and_cache"
down_revision = "0001_dart_rag"
branch_labels = None
depends_on = None


def upgrade():
    data = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("rag_analyses",
                    sa.Column("cache_key", sa.String(64), primary_key=True),
                    sa.Column("rcept_no", sa.String(14), sa.ForeignKey("rag_documents.rcept_no"),
                              nullable=False),
                    sa.Column("result", data, nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_rag_analyses_rcept_no", "rag_analyses", ["rcept_no"])
    op.create_table("rag_chat_tasks",
                    sa.Column("task_id", sa.String(36), primary_key=True),
                    sa.Column("owner", sa.String(64), nullable=False),
                    sa.Column("data", data, nullable=False))
    op.create_index("ix_rag_chat_tasks_owner", "rag_chat_tasks", ["owner"])
    op.create_table("rag_bulk_runs",
                    sa.Column("run_id", sa.String(36), primary_key=True),
                    sa.Column("data", data, nullable=False),
                    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("rag_bulk_items",
                    sa.Column("run_id", sa.String(36), sa.ForeignKey("rag_bulk_runs.run_id"),
                              primary_key=True),
                    sa.Column("rcept_no", sa.String(14), primary_key=True),
                    sa.Column("disclosure", data, nullable=False),
                    sa.Column("status", sa.String(20), nullable=False),
                    sa.Column("error_code", sa.String(80)))


def downgrade():
    for table in ("rag_bulk_items", "rag_bulk_runs", "rag_chat_tasks", "rag_analyses"):
        op.drop_table(table)
