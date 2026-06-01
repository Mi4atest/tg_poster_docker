"""Add availability_status and channel_message_id to products table

Revision ID: add_availability_status
Revises: add_vk_post_fields
Create Date: 2025-01-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_availability_status'
down_revision = 'add_vk_post_fields'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('products', sa.Column('availability_status', sa.String(), nullable=True))
    op.add_column('products', sa.Column('channel_message_id', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('products', 'channel_message_id')
    op.drop_column('products', 'availability_status')
