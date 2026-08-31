"""Дневные точки рынка Avito.

Revision ID: add_avito_market_daily
Revises: add_product_sales
Create Date: 2026-08-31
"""
from alembic import op


revision = "add_avito_market_daily"
down_revision = "add_product_sales"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS avito_market_daily (
            id SERIAL PRIMARY KEY,
            model VARCHAR(80) NOT NULL,
            memory_gb INTEGER NOT NULL,
            region VARCHAR(80) NOT NULL DEFAULT 'Россия',
            observed_on DATE NOT NULL,
            median_rub INTEGER,
            q25_rub INTEGER,
            q75_rub INTEGER,
            used_count INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0,
            quality VARCHAR(8) NOT NULL DEFAULT 'thin',
            source VARCHAR(24) NOT NULL DEFAULT 'manual',
            snapshot_id INTEGER REFERENCES avito_market_snapshots(id) ON DELETE SET NULL,
            created_at TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_avito_market_daily_model_mem_region_day
        ON avito_market_daily (model, memory_gb, region, observed_on)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_avito_market_daily_lookup
        ON avito_market_daily (model, memory_gb, observed_on)
        """
    )
    op.execute(
        """
        ALTER TABLE avito_market_snapshots
        ADD COLUMN IF NOT EXISTS quote_as_of TIMESTAMP
        """
    )
    op.execute(
        """
        ALTER TABLE avito_market_snapshots
        ADD COLUMN IF NOT EXISTS quote_quality VARCHAR(8)
        """
    )


def downgrade():
    op.execute("ALTER TABLE avito_market_snapshots DROP COLUMN IF EXISTS quote_quality")
    op.execute("ALTER TABLE avito_market_snapshots DROP COLUMN IF EXISTS quote_as_of")
    op.execute("DROP INDEX IF EXISTS ix_avito_market_daily_lookup")
    op.execute("DROP INDEX IF EXISTS uq_avito_market_daily_model_mem_region_day")
    op.execute("DROP TABLE IF EXISTS avito_market_daily")
