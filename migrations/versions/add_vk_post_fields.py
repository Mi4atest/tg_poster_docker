"""Add VK post ID and link fields

Revision ID: add_vk_post_fields
Revises: add_product_payment_method
Create Date: 2024-01-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_vk_post_fields'
down_revision = 'add_product_payment_method'
branch_labels = None
depends_on = None


def upgrade():
    # Add VK post ID and link fields to posts table
    op.add_column('posts', sa.Column('vk_post_id', sa.String(), nullable=True))
    op.add_column('posts', sa.Column('vk_post_link', sa.String(), nullable=True))


def downgrade():
    # Remove VK post fields from posts table
    op.drop_column('posts', 'vk_post_link')
    op.drop_column('posts', 'vk_post_id')
