"""Watchlist автообновления рынка Avito и журнал живых запросов.

Revision ID: add_avito_market_watchlist
Revises: add_avito_market_snapshots
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa


revision = "add_avito_market_watchlist"
down_revision = "add_avito_market_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "avito_market_request_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("cache_key", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="manual"),
    )
    op.create_index(
        "ix_avito_market_request_log_requested_at",
        "avito_market_request_log",
        ["requested_at"],
    )

    op.create_table(
        "avito_market_watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("memory_gb", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=8), nullable=False, server_default="daily"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="manual"),
        sa.Column("last_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("next_refresh_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_snapshot_id"],
            ["avito_market_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("model", "memory_gb", name="uq_avito_market_wl_model_mem"),
    )
    op.create_index(
        "ix_avito_market_wl_due",
        "avito_market_watchlist_items",
        ["enabled", "next_refresh_at"],
    )


def downgrade():
    op.drop_index("ix_avito_market_wl_due", table_name="avito_market_watchlist_items")
    op.drop_table("avito_market_watchlist_items")
    op.drop_index(
        "ix_avito_market_request_log_requested_at",
        table_name="avito_market_request_log",
    )
    op.drop_table("avito_market_request_log")
