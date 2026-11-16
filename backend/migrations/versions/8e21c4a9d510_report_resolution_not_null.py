"""Require a review note for resolved reports without rewriting existing history."""

import sqlalchemy as sa
from alembic import op

revision = "8e21c4a9d510"
down_revision = "3f32713066d6"
branch_labels = None
depends_on = None

OLD = (
    "(status = 'open' AND reviewed_by IS NULL AND reviewed_at IS NULL AND "
    "review_note IS NULL AND resolved_action_id IS NULL) OR (status IN "
    "('dismissed','actioned') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT "
    "NULL AND char_length(review_note) BETWEEN 1 AND 2000 AND (status = "
    "'actioned') = (resolved_action_id IS NOT NULL))"
)
NEW = (
    "(status = 'open' AND reviewed_by IS NULL AND reviewed_at IS NULL AND "
    "review_note IS NULL AND resolved_action_id IS NULL) OR (status IN "
    "('dismissed','actioned') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT "
    "NULL AND review_note IS NOT NULL AND char_length(review_note) BETWEEN 1 AND "
    "2000 AND (status = 'actioned') = (resolved_action_id IS NOT NULL))"
)


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE reports IN ACCESS EXCLUSIVE MODE"))
    invalid = bind.scalar(sa.text("SELECT count(*) FROM reports WHERE (" + NEW + ") IS NOT TRUE"))
    if invalid:
        raise RuntimeError(
            f"Upgrade refused: {invalid} reports violate resolution fields; "
            "review the original records before retrying. No reports were changed."
        )
    op.drop_constraint(op.f("ck_reports_resolution"), "reports", type_="check")
    op.create_check_constraint(op.f("ck_reports_resolution"), "reports", NEW)


def downgrade():
    op.drop_constraint(op.f("ck_reports_resolution"), "reports", type_="check")
    op.create_check_constraint(op.f("ck_reports_resolution"), "reports", OLD)
