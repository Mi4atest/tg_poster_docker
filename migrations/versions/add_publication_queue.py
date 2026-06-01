"""Add publication queue fields and table

Revision ID: add_publication_queue
Revises: add_telegram_link_field
Create Date: 2024-12-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'add_publication_queue'
down_revision = 'add_telegram_link_field'
branch_labels = None
depends_on = None


def upgrade():
    # Add queue management fields to posts table
    op.add_column('posts', sa.Column('in_queue', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('posts', sa.Column('queue_status', sa.String(), nullable=True))
    op.add_column('posts', sa.Column('scheduled_at', sa.DateTime(), nullable=True))
    
    # Update existing rows to set in_queue to False
    op.execute("UPDATE posts SET in_queue = FALSE")
    
    # Create publication_queue table
    op.create_table(
        'publication_queue',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('post_id', sa.String(), nullable=False),
        sa.Column('platform', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('priority', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('scheduled_at', sa.DateTime(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create index on post_id and platform for faster queries
    op.create_index('ix_publication_queue_post_id', 'publication_queue', ['post_id'])
    op.create_index('ix_publication_queue_platform', 'publication_queue', ['platform'])
    op.create_index('ix_publication_queue_status', 'publication_queue', ['status'])


def downgrade():
    # Drop indexes
    op.drop_index('ix_publication_queue_status', table_name='publication_queue')
    op.drop_index('ix_publication_queue_platform', table_name='publication_queue')
    op.drop_index('ix_publication_queue_post_id', table_name='publication_queue')
    
    # Drop publication_queue table
    op.drop_table('publication_queue')
    
    # Remove queue management fields from posts table
    op.drop_column('posts', 'scheduled_at')
    op.drop_column('posts', 'queue_status')
    op.drop_column('posts', 'in_queue')

