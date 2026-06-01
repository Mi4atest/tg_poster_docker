"""Add availability_message_ids (JSON list) for multiple channel messages

Revision ID: add_availability_message_ids
Revises: add_availability_status
Create Date: 2025-01-28

"""
from alembic import op
import sqlalchemy as sa


revision = 'add_availability_message_ids'
down_revision = 'add_availability_status'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'products',
        sa.Column('availability_message_ids', sa.Text(), nullable=True)
    )


def downgrade():
    op.drop_column('products', 'availability_message_ids')
