"""add P1 ad detection reliability settings and audio fingerprint table

Revision ID: e7f8a9b0c1d2
Revises: d1e2f3a4b5c6
Create Date: 2026-08-26 18:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_audio_fingerprint",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feed_id", sa.Integer(), nullable=False),
        sa.Column("prompt_tag_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("source_post_id", sa.Integer(), nullable=True),
        sa.Column("source_start", sa.Float(), nullable=True),
        sa.Column("source_end", sa.Float(), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["feed_id"], ["feed.id"]),
        sa.ForeignKeyConstraint(["prompt_tag_id"], ["tag.id"]),
        sa.ForeignKeyConstraint(["source_post_id"], ["post.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "feed_id",
            "fingerprint",
            "kind",
            name="uq_ad_audio_fp_feed_fingerprint_kind",
        ),
    )
    with op.batch_alter_table("ad_audio_fingerprint", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ad_audio_fingerprint_feed_id"), ["feed_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ad_audio_fingerprint_prompt_tag_id"),
            ["prompt_tag_id"],
            unique=False,
        )

    with op.batch_alter_table("llm_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("llm_boundary_refine_model", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "enable_two_stage_classify",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "two_stage_edge_preroll_seconds",
                sa.Integer(),
                nullable=False,
                server_default="120",
            )
        )
        batch_op.add_column(
            sa.Column(
                "two_stage_edge_outro_seconds",
                sa.Integer(),
                nullable=False,
                server_default="60",
            )
        )
        batch_op.add_column(
            sa.Column(
                "two_stage_candidate_pad_segments",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )
        batch_op.add_column(
            sa.Column(
                "enable_ad_audio_fingerprint",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "ad_audio_fp_match_threshold",
                sa.Float(),
                nullable=False,
                server_default="0.15",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ad_audio_fp_min_duration_seconds",
                sa.Float(),
                nullable=False,
                server_default="3.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "enable_ad_gap_detection",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "ad_gap_min_seconds",
                sa.Float(),
                nullable=False,
                server_default="4.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ad_gap_noise_db",
                sa.Integer(),
                nullable=False,
                server_default="-30",
            )
        )
        batch_op.add_column(
            sa.Column(
                "enable_ad_gap_auto_cut",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "jingle_min_seconds",
                sa.Float(),
                nullable=False,
                server_default="1.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "jingle_max_seconds",
                sa.Float(),
                nullable=False,
                server_default="15.0",
            )
        )

    with op.batch_alter_table("post", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ad_detection_debug", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("post", schema=None) as batch_op:
        batch_op.drop_column("ad_detection_debug")

    with op.batch_alter_table("llm_settings", schema=None) as batch_op:
        batch_op.drop_column("jingle_max_seconds")
        batch_op.drop_column("jingle_min_seconds")
        batch_op.drop_column("enable_ad_gap_auto_cut")
        batch_op.drop_column("ad_gap_noise_db")
        batch_op.drop_column("ad_gap_min_seconds")
        batch_op.drop_column("enable_ad_gap_detection")
        batch_op.drop_column("ad_audio_fp_min_duration_seconds")
        batch_op.drop_column("ad_audio_fp_match_threshold")
        batch_op.drop_column("enable_ad_audio_fingerprint")
        batch_op.drop_column("two_stage_candidate_pad_segments")
        batch_op.drop_column("two_stage_edge_outro_seconds")
        batch_op.drop_column("two_stage_edge_preroll_seconds")
        batch_op.drop_column("enable_two_stage_classify")
        batch_op.drop_column("llm_boundary_refine_model")

    with op.batch_alter_table("ad_audio_fingerprint", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ad_audio_fingerprint_prompt_tag_id"))
        batch_op.drop_index(batch_op.f("ix_ad_audio_fingerprint_feed_id"))

    op.drop_table("ad_audio_fingerprint")
