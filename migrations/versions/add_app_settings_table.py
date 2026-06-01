"""Add app settings table

Revision ID: add_app_settings_table
Revises: add_availability_message_ids
Create Date: 2026-05-01 16:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "add_app_settings_table"
down_revision = "add_availability_message_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("encrypted_secrets", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        INSERT INTO app_settings (id, config, encrypted_secrets, created_at, updated_at)
        VALUES (1, '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )


def downgrade():
    op.drop_table("app_settings")
