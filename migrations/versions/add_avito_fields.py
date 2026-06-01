"""Add Avito integration columns to posts and products

Revision ID: add_avito_fields
Revises: merge_max_heads
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa


revision = "add_avito_fields"
down_revision = "merge_max_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("posts", sa.Column("is_published_avito", sa.Boolean(), nullable=True, server_default="false"))
    op.add_column("posts", sa.Column("published_avito_at", sa.DateTime(), nullable=True))
    op.add_column("posts", sa.Column("avito_item_id", sa.String(), nullable=True))
    op.add_column("posts", sa.Column("avito_url", sa.String(), nullable=True))
    op.add_column("posts", sa.Column("avito_draft", sa.JSON(), nullable=True))
    op.add_column("products", sa.Column("avito_item_id", sa.String(), nullable=True))
    op.add_column("products", sa.Column("avito_url", sa.String(), nullable=True))
    op.execute("UPDATE posts SET is_published_avito = FALSE WHERE is_published_avito IS NULL")


def downgrade():
    op.drop_column("products", "avito_url")
    op.drop_column("products", "avito_item_id")
    op.drop_column("posts", "avito_draft")
    op.drop_column("posts", "avito_url")
    op.drop_column("posts", "avito_item_id")
    op.drop_column("posts", "published_avito_at")
    op.drop_column("posts", "is_published_avito")
