"""Add max_share_url for public MAX post links

Revision ID: add_max_share_url
Revises: add_new_menu_buttons
Create Date: 2026-05-09 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "add_max_share_url"
down_revision = "add_new_menu_buttons"
branch_labels = None
depends_on = None


def upgrade():
    # Колонки могли уже появиться через app.db.migrate.ensure_database_schema — не падаем.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'posts' AND column_name = 'max_share_url'
            ) THEN
                ALTER TABLE posts ADD COLUMN max_share_url VARCHAR;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products' AND column_name = 'max_share_url'
            ) THEN
                ALTER TABLE products ADD COLUMN max_share_url VARCHAR;
            END IF;
        END $$;
        """
    )


def downgrade():
    op.drop_column("products", "max_share_url")
    op.drop_column("posts", "max_share_url")
