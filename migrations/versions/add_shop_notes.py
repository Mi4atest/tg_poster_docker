"""shop_notes: напоминалки на главном экране

Revision ID: add_shop_notes
Revises: add_product_archive_kind
Create Date: 2026-08-27
"""
from alembic import op


revision = "add_shop_notes"
down_revision = "add_product_archive_kind"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_notes (
            id SERIAL PRIMARY KEY,
            body TEXT NOT NULL,
            category VARCHAR(32),
            is_done BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP,
            done_at TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_shop_notes_is_done
        ON shop_notes (is_done)
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_shop_notes_is_done")
    op.execute("DROP TABLE IF EXISTS shop_notes")
