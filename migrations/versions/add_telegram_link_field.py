"""Add telegram_link field

Revision ID: add_telegram_link_field
Revises: add_instagram_fields
Create Date: 2024-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_telegram_link_field'
down_revision = 'add_instagram_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Add telegram_link field to posts table
    op.add_column('posts', sa.Column('telegram_link', sa.String(), nullable=True))


def downgrade():
    # Remove telegram_link field from posts table
    op.drop_column('posts', 'telegram_link')

