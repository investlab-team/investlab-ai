"""Keep large evidence in private object storage when configured (local DB still works)."""
import sqlalchemy as sa
from alembic import op

revision = "0004_evidence_objects"
down_revision = "0003_analysis_leases"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("rag_documents") as batch:
        batch.add_column(sa.Column("original_object_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("chunks_object_key", sa.Text(), nullable=True))
        batch.alter_column("original_zip", existing_type=sa.LargeBinary(), nullable=True)
        batch.create_check_constraint("rag_document_original_present",
                                      "original_zip IS NOT NULL OR original_object_key IS NOT NULL")


def downgrade():
    # Refuse to discard pointers while any evidence is stored externally.
    count = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM rag_documents WHERE original_zip IS NULL"
    )).scalar_one()
    if count:
        raise RuntimeError("Restore external evidence into PostgreSQL before downgrading")
    op.drop_constraint("rag_document_original_present", "rag_documents", type_="check")
    op.alter_column("rag_documents", "original_zip", existing_type=sa.LargeBinary(), nullable=False)
    op.drop_column("rag_documents", "chunks_object_key")
    op.drop_column("rag_documents", "original_object_key")
