"""add indexes for visit & todo metrics facts

Revision ID: 9d1e2f3a4b56
Revises: 69ac02144005
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9d1e2f3a4b56"
down_revision: str = "69ac02144005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # crm_visit_metrics_facts: (anchor, grain, subject_type, subject_id, metric, weekday_iso, period_start)
    op.create_index(
        "idx_visit_company_week_anchor_metric",
        "crm_visit_metrics_facts",
        ["anchor", "grain", "subject_type", "subject_id", "metric", "weekday_iso", "period_start"],
        unique=False,
    )

    # crm_todo_metrics_facts: (anchor, grain, subject_type, subject_id, metric, data_source, period_start)
    op.create_index(
        "idx_todo_company_week_anchor_metric_ds",
        "crm_todo_metrics_facts",
        ["anchor", "grain", "subject_type", "subject_id", "metric", "data_source", "period_start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_todo_company_week_anchor_metric_ds", table_name="crm_todo_metrics_facts")
    op.drop_index("idx_visit_company_week_anchor_metric", table_name="crm_visit_metrics_facts")

