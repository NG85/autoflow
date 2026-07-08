"""OAuth data-scope permission helpers for business-native entities."""

from app.permissions.follow_up_scope_translator import ScopeSql, linked_crm_follow_up_sql, translate_follow_up_scope_to_sql

__all__ = [
    "ScopeSql",
    "linked_crm_follow_up_sql",
    "translate_follow_up_scope_to_sql",
]
