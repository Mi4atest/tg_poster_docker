"""Журнал продаж новых товаров для сводки месяца.

Revision ID: add_product_sales
Revises: add_avito_market_watchlist
Create Date: 2026-08-30
"""
from alembic import op


revision = "add_product_sales"
down_revision = "add_avito_market_watchlist"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_sales (
            id SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            name VARCHAR NOT NULL,
            collection_name VARCHAR,
            price VARCHAR,
            sold_at TIMESTAMP NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_product_sales_sold_at ON product_sales (sold_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_product_sales_product_id ON product_sales (product_id)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_product_sales_product_id")
    op.execute("DROP INDEX IF EXISTS ix_product_sales_sold_at")
    op.execute("DROP TABLE IF EXISTS product_sales")
