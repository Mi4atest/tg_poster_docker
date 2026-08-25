"""Add Avito integration columns to posts and products

Revision ID: add_avito_fields
Revises: merge_max_heads
Create Date: 2026-05-12
"""
from alembic import op


revision = "add_avito_fields"
down_revision = "merge_max_heads"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = '{table}'
                  AND column_name = '{column}'
            ) THEN
                ALTER TABLE {table} ADD COLUMN {ddl};
            END IF;
        END $$;
        """
    )


def upgrade():
    _add_column_if_missing(
        "posts", "is_published_avito", "is_published_avito BOOLEAN DEFAULT 'false'"
    )
    _add_column_if_missing("posts", "published_avito_at", "published_avito_at TIMESTAMP")
    _add_column_if_missing("posts", "avito_item_id", "avito_item_id VARCHAR")
    _add_column_if_missing("posts", "avito_url", "avito_url VARCHAR")
    _add_column_if_missing("posts", "avito_draft", "avito_draft JSON")
    _add_column_if_missing("products", "avito_item_id", "avito_item_id VARCHAR")
    _add_column_if_missing("products", "avito_url", "avito_url VARCHAR")
    op.execute("UPDATE posts SET is_published_avito = FALSE WHERE is_published_avito IS NULL")


def downgrade():
    op.drop_column("products", "avito_url")
    op.drop_column("products", "avito_item_id")
    op.drop_column("posts", "avito_draft")
    op.drop_column("posts", "avito_url")
    op.drop_column("posts", "avito_item_id")
    op.drop_column("posts", "published_avito_at")
    op.drop_column("posts", "is_published_avito")
