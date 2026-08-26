"""archive_kind: продажа или перемещение при снятии б/у с витрины

Revision ID: add_product_archive_kind
Revises: add_product_price_history
Create Date: 2026-08-27
"""
from alembic import op


revision = "add_product_archive_kind"
down_revision = "add_product_price_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products'
                  AND column_name = 'archive_kind'
            ) THEN
                ALTER TABLE products ADD COLUMN archive_kind VARCHAR;
            END IF;
        END $$;
        """
    )


def downgrade():
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products'
                  AND column_name = 'archive_kind'
            ) THEN
                ALTER TABLE products DROP COLUMN archive_kind;
            END IF;
        END $$;
        """
    )
