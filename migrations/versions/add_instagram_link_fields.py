"""Add instagram_link and instagram_media_id fields

Revision ID: add_instagram_link_fields
Revises: merge_max_heads
Create Date: 2026-06-13 12:00:00.000000
"""
from alembic import op


revision = "add_instagram_link_fields"
down_revision = "merge_max_heads"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("posts", "products"):
        op.execute(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = '{table}'
                      AND column_name = 'instagram_link'
                ) THEN
                    ALTER TABLE {table} ADD COLUMN instagram_link VARCHAR;
                END IF;
            END $$;
            """
        )
        op.execute(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = '{table}'
                      AND column_name = 'instagram_media_id'
                ) THEN
                    ALTER TABLE {table} ADD COLUMN instagram_media_id VARCHAR;
                END IF;
            END $$;
            """
        )


def downgrade():
    op.drop_column("products", "instagram_media_id")
    op.drop_column("products", "instagram_link")
    op.drop_column("posts", "instagram_media_id")
    op.drop_column("posts", "instagram_link")
