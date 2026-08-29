"""Add persistent Avito market estimate cache.

Revision ID: add_avito_market_snapshots
Revises: add_shop_notes
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = "add_avito_market_snapshots"
down_revision = "add_shop_notes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "avito_market_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cache_key", sa.String(length=160), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("memory_gb", sa.Integer(), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="success"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outlier_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("median_rub", sa.Integer(), nullable=True),
        sa.Column("q25_rub", sa.Integer(), nullable=True),
        sa.Column("q75_rub", sa.Integer(), nullable=True),
        sa.Column("private_summary", sa.JSON(), nullable=True),
        sa.Column("business_summary", sa.JSON(), nullable=True),
        sa.Column("listing_audit", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_after", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_avito_market_snapshots_cache_key",
        "avito_market_snapshots",
        ["cache_key"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_avito_market_snapshots_cache_key",
        table_name="avito_market_snapshots",
    )
    op.drop_table("avito_market_snapshots")
