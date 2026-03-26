"""add crm_review_opp_audit_log table

Revision ID: b8d9f2c4a7e0
Revises: 9d1e2f3a4b56
Create Date: 2026-03-25 00:00:00
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = "b8d9f2c4a7e0"
down_revision = "9d1e2f3a4b56"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_review_opp_audit_log",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column(
            "session_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
            comment="FK → crm_review_session.unique_id",
        ),
        sa.Column(
            "change_scope",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
            comment="Field name that was changed",
        ),
        sa.Column(
            "old_value",
            sa.Text(),
            nullable=True,
            comment="Previous value (string representation)",
        ),
        sa.Column(
            "new_value",
            sa.Text(),
            nullable=True,
            comment="New value (string representation)",
        ),
        sa.Column(
            "change_type",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
            server_default=sa.text("'UPDATE'"),
            comment="Type: UPDATE",
        ),
        sa.Column(
            "edit_phase",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=True,
            comment="initial/meeting - which phase the change was made",
        ),
        sa.Column(
            "updated_by",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
            comment="User name who made the change",
        ),
        sa.Column(
            "updated_by_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
            comment="User ID who made the change",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="Audit log for branch snapshot modifications",
    )
    op.create_index(
        "idx_session_id",
        "crm_review_opp_audit_log",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "idx_updated_by_id",
        "crm_review_opp_audit_log",
        ["updated_by_id"],
        unique=False,
    )
    op.create_index(
        "idx_updated_at",
        "crm_review_opp_audit_log",
        ["updated_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_updated_at", table_name="crm_review_opp_audit_log")
    op.drop_index("idx_updated_by_id", table_name="crm_review_opp_audit_log")
    op.drop_index("idx_session_id", table_name="crm_review_opp_audit_log")
    op.drop_table("crm_review_opp_audit_log")

