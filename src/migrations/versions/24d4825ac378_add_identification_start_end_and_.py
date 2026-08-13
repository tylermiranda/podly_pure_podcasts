"""add identification start/end and transcript segment words

Revision ID: 24d4825ac378
Revises: 6c491b61edf2
Create Date: 2026-08-13 11:47:43.730481

"""

import sqlalchemy as sa
from alembic import op

revision = "24d4825ac378"
down_revision = "6c491b61edf2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("identification", schema=None) as batch_op:
        batch_op.add_column(sa.Column("start_time", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("end_time", sa.Float(), nullable=True))

    with op.batch_alter_table("transcript_segment", schema=None) as batch_op:
        batch_op.add_column(sa.Column("words", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("transcript_segment", schema=None) as batch_op:
        batch_op.drop_column("words")

    with op.batch_alter_table("identification", schema=None) as batch_op:
        batch_op.drop_column("end_time")
        batch_op.drop_column("start_time")
