"""Add price_changed_at and product_price_history for stale price tracking

Revision ID: add_product_price_history
Revises: add_avito_fields, add_instagram_link_fields
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa


revision = "add_product_price_history"
down_revision = ("add_avito_fields", "add_instagram_link_fields")
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("price_changed_at", sa.DateTime(), nullable=True))

    op.create_table(
        "product_price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("old_price", sa.String(), nullable=True),
        sa.Column("new_price", sa.String(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_price_history_product_changed",
        "product_price_history",
        ["product_id", "changed_at"],
    )

    # Backfill: price_changed_at = created_at; initial publication history row
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
        """
    )
    # Products without price still get price_changed_at; skip empty history rows


def downgrade():
    op.drop_index("ix_product_price_history_product_changed", table_name="product_price_history")
    op.drop_table("product_price_history")
    op.drop_column("products", "price_changed_at")
