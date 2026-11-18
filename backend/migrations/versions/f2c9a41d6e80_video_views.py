"""Durable video totals, playback sessions and rolling deduplication.

Revision ID: f2c9a41d6e80
Revises: d4a6c2e91f08
"""

import sqlalchemy as sa
from alembic import op

revision = "f2c9a41d6e80"
down_revision = "d4a6c2e91f08"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "videos", sa.Column("views_count", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.create_check_constraint("views_count", "videos", "views_count >= 0")
    op.create_table(
        "video_view_sessions",
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("viewer_key", sa.String(64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("position_ms", sa.BigInteger(), nullable=False),
        sa.Column("played_ms", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("video_id", "viewer_key"),
        sa.UniqueConstraint("session_id"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "played_ms >= 0 AND position_ms >= 0 AND sequence >= 0", name="progress"
        ),
        sa.CheckConstraint("rate BETWEEN 0.25 AND 2", name="rate"),
    )
    op.create_index("ix_video_view_sessions_expires_at", "video_view_sessions", ["expires_at"])
    op.create_table(
        "video_view_receipts",
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("viewer_key", sa.String(64), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("video_id", "viewer_key"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_video_view_receipts_counted_at", "video_view_receipts", ["counted_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM videos WHERE views_count > 0)")):
        raise RuntimeError("Downgrade refused: historical video views exist")
    op.drop_table("video_view_receipts")
    op.drop_table("video_view_sessions")
    op.drop_constraint(op.f("ck_videos_views_count"), "videos", type_="check")
    op.drop_column("videos", "views_count")
