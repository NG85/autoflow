"""Add foreign keys to users after OAuth creates the table.

Revision ID: b7e4a91c2d10
Revises: c6821ddd604a
Create Date: 2026-11-17 14:55:00.000000

The PostgreSQL baseline already declares these FKs for fresh installs. Qingflow
applied the baseline before ``users`` existed, so the constraints were absent.
OAuth now owns ``users``; this revision adds the missing FKs and clears orphan
references that would block constraint creation.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "b7e4a91c2d10"
down_revision = "c6821ddd604a"
branch_labels = None
depends_on = None

# (table, column, nullable, constraint_name)
_USER_FKS: list[tuple[str, str, bool, str]] = [
    ("crm_sales_visit_records", "recorder_id", False, "fk_recorder_id"),
    ("api_keys", "user_id", False, "api_keys_user_id_fkey"),
    ("data_sources", "user_id", True, "data_sources_user_id_fkey"),
    ("document_contents", "user_id", False, "document_contents_user_id_fkey"),
    ("evaluation_datasets", "user_id", True, "evaluation_datasets_user_id_fkey"),
    ("evaluation_tasks", "user_id", True, "evaluation_tasks_user_id_fkey"),
    ("knowledge_bases", "created_by", True, "knowledge_bases_created_by_fkey"),
    ("knowledge_bases", "deleted_by", True, "knowledge_bases_deleted_by_fkey"),
    ("knowledge_bases", "updated_by", True, "knowledge_bases_updated_by_fkey"),
    (
        "notification_cc_rules",
        "scope_user_id",
        True,
        "notification_cc_rules_scope_user_id_fkey",
    ),
    (
        "notification_delivery_preferences",
        "user_id",
        False,
        "notification_delivery_preferences_user_id_fkey",
    ),
    ("uploads", "user_id", True, "uploads_user_id_fkey"),
    ("chats", "user_id", True, "chats_user_id_fkey"),
    ("customer_documents", "uploader_id", False, "customer_documents_uploader_id_fkey"),
    ("file_permissions", "granted_by", True, "file_permissions_granted_by_fkey"),
    ("file_permissions", "user_id", False, "file_permissions_user_id_fkey"),
    (
        "notification_cc_rule_recipients",
        "user_id",
        False,
        "notification_cc_rule_recipients_user_id_fkey",
    ),
    ("chat_messages", "user_id", True, "chat_messages_user_id_fkey"),
    (
        "document_source_files",
        "confirmed_by",
        False,
        "document_source_files_confirmed_by_fkey",
    ),
    ("feedbacks", "user_id", True, "feedbacks_user_id_fkey"),
]


def _constraint_exists(connection, name: str) -> bool:
    return (
        connection.execute(
            text(
                """
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_schema = current_schema()
                  AND constraint_name = :name
                  AND constraint_type = 'FOREIGN KEY'
                """
            ),
            {"name": name},
        ).scalar()
        is not None
    )


def _clear_orphans(connection, table: str, column: str, nullable: bool) -> None:
    if nullable:
        connection.execute(
            text(
                f"""
                UPDATE "{table}" AS t
                SET "{column}" = NULL
                WHERE t."{column}" IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM users u WHERE u.id = t."{column}"
                  )
                """
            )
        )
        return

    # NOT NULL columns: drop rows that reference missing users.
    connection.execute(
        text(
            f"""
            DELETE FROM "{table}" AS t
            WHERE t."{column}" IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM users u WHERE u.id = t."{column}"
              )
            """
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    users_exists = connection.execute(text("SELECT to_regclass('users')")).scalar()
    if users_exists is None:
        raise RuntimeError(
            "users table is missing; deploy aptsell-oauth PostgreSQL migrations first"
        )

    for table, column, nullable, name in _USER_FKS:
        if _constraint_exists(connection, name):
            continue
        _clear_orphans(connection, table, column, nullable)
        op.create_foreign_key(
            name,
            table,
            "users",
            [column],
            ["id"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table, _column, _nullable, name in reversed(_USER_FKS):
        if _constraint_exists(connection, name):
            op.drop_constraint(name, table, type_="foreignkey")
