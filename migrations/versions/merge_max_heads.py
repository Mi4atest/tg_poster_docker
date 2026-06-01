"""Merge alembic heads add_max_fields and add_max_share_url

Revision ID: merge_max_heads
Revises: add_max_fields, add_max_share_url
Create Date: 2026-05-09 14:00:00.000000
"""
from alembic import op

revision = "merge_max_heads"
down_revision = ("add_max_fields", "add_max_share_url")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
