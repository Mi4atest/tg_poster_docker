"""Add archived_at and telegram_link to products table

Revision ID: add_product_archived_at_and_telegram_link
Revises: add_products_table
Create Date: 2025-12-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_product_archived_at_and_telegram_link'
down_revision = 'add_products_table'
branch_labels = None
depends_on = None


def upgrade():
    # Add archived_at column
    op.add_column('products', sa.Column('archived_at', sa.DateTime(), nullable=True))
    
    # Add telegram_link column
    op.add_column('products', sa.Column('telegram_link', sa.String(), nullable=True))


def downgrade():
    # Remove columns
    op.drop_column('products', 'telegram_link')
    op.drop_column('products', 'archived_at')

