"""Add Max publication fields

Revision ID: add_max_fields
Revises: add_publication_queue
Create Date: 2026-04-29 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "add_max_fields"
down_revision = "add_publication_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("posts", sa.Column("is_published_max", sa.Boolean(), nullable=True, server_default="false"))
    op.add_column("posts", sa.Column("published_max_at", sa.DateTime(), nullable=True))
    op.add_column("posts", sa.Column("max_link", sa.String(), nullable=True))
    op.add_column("products", sa.Column("max_link", sa.String(), nullable=True))
    op.execute("UPDATE posts SET is_published_max = FALSE WHERE is_published_max IS NULL")


def downgrade():
    op.drop_column("products", "max_link")
    op.drop_column("posts", "max_link")
    op.drop_column("posts", "published_max_at")
    op.drop_column("posts", "is_published_max")
