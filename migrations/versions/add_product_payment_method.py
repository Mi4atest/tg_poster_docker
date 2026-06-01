"""Add payment_method to products table

Revision ID: add_product_payment_method
Revises: add_product_archived_at_and_telegram_link
Create Date: 2025-12-03 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_product_payment_method'
down_revision = 'add_product_archived_at_and_telegram_link'
branch_labels = None
depends_on = None


def upgrade():
    # Add payment_method column
    op.add_column('products', sa.Column('payment_method', sa.String(), nullable=True))
    
    # Add final_price column (цена с учетом способа оплаты)
    op.add_column('products', sa.Column('final_price', sa.String(), nullable=True))


def downgrade():
    # Remove columns
    op.drop_column('products', 'final_price')
    op.drop_column('products', 'payment_method')

