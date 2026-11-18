"""Comment threads and idempotent comment likes, preserving existing interactions.

Revision ID: b7d2e8f91a60
Revises: 8e21c4a9d510
"""

import sqlalchemy as sa
from alembic import op

revision = "b7d2e8f91a60"
down_revision = "8e21c4a9d510"
branch_labels = None
depends_on = None


def upgrade():
    # NULL makes every existing comment a root, without rewriting its text.
    op.add_column("comments", sa.Column("parent_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint("uq_comments_id_video", "comments", ["id", "video_id"])
    op.create_foreign_key(
        "fk_comments_parent_video",
        "comments",
        "comments",
        ["parent_id", "video_id"],
        ["id", "video_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint("not_own_parent", "comments", "parent_id IS NULL OR parent_id <> id")
    op.create_index(
        "ix_comments_thread_created", "comments", ["video_id", "parent_id", "created_at", "id"]
    )
    op.drop_constraint("fk_comments_video_id_videos", "comments", type_="foreignkey")
    op.create_foreign_key(
        "fk_comments_video_id_videos",
        "comments",
        "videos",
        ["video_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "comment_likes",
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("comment_id", "user_id"),
    )


def downgrade():
    connection = op.get_bind()
    # Refuse data loss; empty-schema migration roundtrips remain possible.
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM comment_likes)")
    ) or connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM comments WHERE parent_id IS NOT NULL)")
    ):
        raise RuntimeError("Downgrade refused: comment replies or likes would be lost")
    op.drop_table("comment_likes")
    op.drop_index("ix_comments_thread_created", table_name="comments")
    op.drop_constraint(op.f("ck_comments_not_own_parent"), "comments", type_="check")
    op.drop_constraint("fk_comments_parent_video", "comments", type_="foreignkey")
    op.drop_constraint("uq_comments_id_video", "comments", type_="unique")
    op.drop_column("comments", "parent_id")
    op.drop_constraint("fk_comments_video_id_videos", "comments", type_="foreignkey")
    op.create_foreign_key("fk_comments_video_id_videos", "comments", "videos", ["video_id"], ["id"])
