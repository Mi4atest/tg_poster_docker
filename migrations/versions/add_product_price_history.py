"""Add price_changed_at and product_price_history for stale price tracking

Revision ID: add_product_price_history
Revises: add_avito_fields, add_instagram_link_fields
Create Date: 2026-07-13
"""
from alembic import op


revision = "add_product_price_history"
down_revision = ("add_avito_fields", "add_instagram_link_fields")
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'products'
                  AND column_name = 'price_changed_at'
            ) THEN
                ALTER TABLE products ADD COLUMN price_changed_at TIMESTAMP;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_price_history (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            old_price VARCHAR,
            new_price VARCHAR NOT NULL,
            changed_at TIMESTAMP NOT NULL,
            source VARCHAR NOT NULL DEFAULT 'manual'
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_product_price_history_product_changed
        ON product_price_history (product_id, changed_at)
        """
    )

    op.execute(
        """
        UPDATE products SET price_changed_at = created_at WHERE price_changed_at IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO product_price_history (product_id, old_price, new_price, changed_at, source)
        SELECT id, NULL, COALESCE(price, ''), created_at, 'publication'
        FROM products
        WHERE COALESCE(price, '') != ''
          AND NOT EXISTS (
            SELECT 1 FROM product_price_history h
            WHERE h.product_id = products.id AND h.source = 'publication'
          )
        """
    )


def downgrade():
    op.drop_index("ix_product_price_history_product_changed", table_name="product_price_history")
    op.drop_table("product_price_history")
    op.drop_column("products", "price_changed_at")
