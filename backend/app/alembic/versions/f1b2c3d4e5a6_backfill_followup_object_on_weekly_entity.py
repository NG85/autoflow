"""backfill followup_object_* on weekly followup entity summary from account/partner

Revision ID: f1b2c3d4e5a6
Revises: e6f7a8b9c0d1
Create Date: 2026-07-15

"""

from alembic import op
from sqlalchemy import text


revision = "f1b2c3d4e5a6"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None

BATCH_SIZE = 2000


def upgrade() -> None:
    conn = op.get_bind()

    # 1) account 行（有 account_id；含 entity_type=opportunity 带客户的行）
    while True:
        result = conn.execute(text("""
            UPDATE crm_weekly_followup_entity_summary
            SET followup_object_type = 'end_customer',
                followup_object_id   = account_id,
                followup_object_name = account_name
            WHERE followup_object_type IS NULL
              AND account_id IS NOT NULL AND TRIM(account_id) <> ''
            LIMIT :batch
        """), {"batch": BATCH_SIZE})
        if result.rowcount == 0:
            break

    # 2) partner 行（无 account_id，有 partner_id）
    while True:
        result = conn.execute(text("""
            UPDATE crm_weekly_followup_entity_summary
            SET followup_object_type = 'partner',
                followup_object_id   = partner_id,
                followup_object_name = partner_name
            WHERE followup_object_type IS NULL
              AND (account_id IS NULL OR TRIM(account_id) = '')
              AND partner_id IS NOT NULL AND TRIM(partner_id) <> ''
            LIMIT :batch
        """), {"batch": BATCH_SIZE})
        if result.rowcount == 0:
            break


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        UPDATE crm_weekly_followup_entity_summary
        SET followup_object_type = NULL,
            followup_object_id   = NULL,
            followup_object_name = NULL
        WHERE followup_object_type IN ('end_customer', 'partner')
    """))
