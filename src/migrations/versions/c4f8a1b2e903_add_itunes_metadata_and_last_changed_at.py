"""add itunes metadata and last_changed_at to feed

Revision ID: c4f8a1b2e903
Revises: 8a53af8667d7
Create Date: 2026-08-25 13:28:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4f8a1b2e903"
down_revision = "8a53af8667d7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("feed", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rss_language", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("itunes_explicit", sa.String(length=8), nullable=True)
        )
        batch_op.add_column(
            sa.Column("itunes_type", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(sa.Column("itunes_categories", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "last_changed_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
        )

    # Drop server default after backfill so the ORM default remains the source of truth.
    with op.batch_alter_table("feed", schema=None) as batch_op:
        batch_op.alter_column("last_changed_at", server_default=None)


def downgrade():
    with op.batch_alter_table("feed", schema=None) as batch_op:
        batch_op.drop_column("last_changed_at")
        batch_op.drop_column("itunes_categories")
        batch_op.drop_column("itunes_type")
        batch_op.drop_column("itunes_explicit")
        batch_op.drop_column("rss_language")
