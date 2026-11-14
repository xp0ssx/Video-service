"""Atomic role audit; video moderation is introduced with videos later.

Revision ID: 9a72f8c41d03
Revises: e0b98fe493dc
"""

import sqlalchemy as sa
from alembic import op

revision = "9a72f8c41d03"
down_revision = "e0b98fe493dc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "moderation_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("previous_value", sa.String(16), nullable=False),
        sa.Column("new_value", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_actions")),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_moderation_actions_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            name=op.f("fk_moderation_actions_target_user_id_users"),
        ),
        sa.CheckConstraint("action = 'set_role'", name=op.f("ck_moderation_actions_action")),
    )
    op.create_index(
        op.f("ix_moderation_actions_target_user_id"), "moderation_actions", ["target_user_id"]
    )


def downgrade():
    op.drop_index(op.f("ix_moderation_actions_target_user_id"), table_name="moderation_actions")
    op.drop_table("moderation_actions")
