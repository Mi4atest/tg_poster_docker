"""Add new_menu_buttons table and products.custom_button_id

Revision ID: add_new_menu_buttons
Revises: add_app_settings_table
Create Date: 2026-05-01 20:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "add_new_menu_buttons"
down_revision = "add_app_settings_table"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "new_menu_buttons",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("parent_path", sa.String(length=512), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_new_menu_buttons_parent_path", "new_menu_buttons", ["parent_path"])

    op.add_column(
        "products",
        sa.Column("custom_button_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_products_custom_button_id",
        "products",
        "new_menu_buttons",
        ["custom_button_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_products_custom_button_id", "products", ["custom_button_id"])


def downgrade():
    op.drop_index("ix_products_custom_button_id", table_name="products")
    op.drop_constraint("fk_products_custom_button_id", "products", type_="foreignkey")
    op.drop_column("products", "custom_button_id")
    op.drop_index("ix_new_menu_buttons_parent_path", table_name="new_menu_buttons")
    op.drop_table("new_menu_buttons")
