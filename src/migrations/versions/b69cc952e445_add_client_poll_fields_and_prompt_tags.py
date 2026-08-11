"""add client poll fields and prompt tags

Revision ID: b69cc952e445
Revises: a9f8e7d6c5b4
Create Date: 2026-08-11 11:27:22.572375

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b69cc952e445"
down_revision = "a9f8e7d6c5b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tag",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tag_name"),
    )
    with op.batch_alter_table("feed", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("last_client_polled_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_client_name", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("prompt_tag_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_feed_prompt_tag_id_tag",
            "tag",
            ["prompt_tag_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("feed", schema=None) as batch_op:
        batch_op.drop_constraint("fk_feed_prompt_tag_id_tag", type_="foreignkey")
        batch_op.drop_column("prompt_tag_id")
        batch_op.drop_column("last_client_name")
        batch_op.drop_column("last_client_polled_at")

    op.drop_table("tag")
