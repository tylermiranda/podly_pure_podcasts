"""add ad_creative table and ad verify LLM settings

Revision ID: d1e2f3a4b5c6
Revises: c4f8a1b2e903
Create Date: 2026-08-26 15:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c4f8a1b2e903"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_creative",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feed_id", sa.Integer(), nullable=False),
        sa.Column("prompt_tag_id", sa.Integer(), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("sample_text", sa.Text(), nullable=True),
        sa.Column("source_post_id", sa.Integer(), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["feed_id"], ["feed.id"]),
        sa.ForeignKeyConstraint(["prompt_tag_id"], ["tag.id"]),
        sa.ForeignKeyConstraint(["source_post_id"], ["post.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "feed_id", "fingerprint", name="uq_ad_creative_feed_fingerprint"
        ),
    )
    with op.batch_alter_table("ad_creative", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ad_creative_feed_id"), ["feed_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ad_creative_prompt_tag_id"),
            ["prompt_tag_id"],
            unique=False,
        )

    with op.batch_alter_table("llm_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "enable_ad_verify",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("llm_verify_model", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("llm_settings", schema=None) as batch_op:
        batch_op.drop_column("llm_verify_model")
        batch_op.drop_column("enable_ad_verify")

    with op.batch_alter_table("ad_creative", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ad_creative_prompt_tag_id"))
        batch_op.drop_index(batch_op.f("ix_ad_creative_feed_id"))

    op.drop_table("ad_creative")
