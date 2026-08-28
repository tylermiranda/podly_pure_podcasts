"""add auto_generate_prompt_tag to llm_settings

Revision ID: e5502cd9c548
Revises: e7f8a9b0c1d2
Create Date: 2026-08-28 07:25:47.567929

"""

import sqlalchemy as sa
from alembic import op

revision = "e5502cd9c548"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("llm_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_generate_prompt_tag",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade():
    with op.batch_alter_table("llm_settings", schema=None) as batch_op:
        batch_op.drop_column("auto_generate_prompt_tag")
