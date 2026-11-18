"""Permanent video tombstones and durable media cleanup.

Revision ID: d4a6c2e91f08
Revises: b7d2e8f91a60
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a6c2e91f08"
down_revision = "b7d2e8f91a60"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "video_deletions",
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','retry','complete')", name="status"),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"]),
        sa.PrimaryKeyConstraint("video_id"),
    )
    op.create_index("ix_video_deletions_run_after", "video_deletions", ["run_after"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM video_deletions)")):
        raise RuntimeError("Downgrade refused: permanent video deletions exist")
    op.drop_table("video_deletions")
    op.drop_column("videos", "deleted_at")
