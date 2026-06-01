"""Add products table

Revision ID: add_products_table
Revises: add_publication_queue
Create Date: 2025-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_products_table'
down_revision = 'add_publication_queue'
branch_labels = None
depends_on = None


def upgrade():
    # Create products table
    op.create_table(
        'products',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('post_id', sa.String(), nullable=False),
        sa.Column('vk_product_id', sa.Integer(), nullable=True),
        sa.Column('vk_product_link', sa.String(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('price', sa.String(), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('category_name', sa.String(), nullable=True),
        sa.Column('collection_id', sa.Integer(), nullable=True),
        sa.Column('collection_name', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('ix_products_post_id', 'products', ['post_id'])
    op.create_index('ix_products_vk_product_id', 'products', ['vk_product_id'])
    op.create_index('ix_products_status', 'products', ['status'])


def downgrade():
    # Drop indexes
    op.drop_index('ix_products_status', table_name='products')
    op.drop_index('ix_products_vk_product_id', table_name='products')
    op.drop_index('ix_products_post_id', table_name='products')
    
    # Drop products table
    op.drop_table('products')


