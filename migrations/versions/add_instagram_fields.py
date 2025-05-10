"""Add Instagram fields

Revision ID: add_instagram_fields
Revises: 
Create Date: 2023-05-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_instagram_fields'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add Instagram fields to posts table
    op.add_column('posts', sa.Column('is_published_instagram', sa.Boolean(), nullable=True, default=False))
    op.add_column('posts', sa.Column('published_instagram_at', sa.DateTime(), nullable=True))
    
    # Update existing rows to set is_published_instagram to False
    op.execute("UPDATE posts SET is_published_instagram = FALSE")


def downgrade():
    # Remove Instagram fields from posts table
    op.drop_column('posts', 'published_instagram_at')
    op.drop_column('posts', 'is_published_instagram')
