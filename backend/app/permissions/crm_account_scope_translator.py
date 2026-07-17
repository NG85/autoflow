"""将 OAuth data-scope（entity=crm_account）译为本地联系人列表 SQL WHERE。

本地联系人可见性继承所属客户：``local_contacts.customer_id`` ↔ ``crm_data_authority.data_id``。
真源：aptsell-oauth docs/examples/crm_translate_scope_to_sql.py、api-permission-data-scope.md。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CRM_ENTITY_ACCOUNT = "crm_account"
MIRROR_TABLE = "crm_data_authority"
DELETE_FLAG_NOT_DELETED = "0"
LOCAL_CONTACTS_TABLE = "local_contacts"
LOCAL_CONTACTS_ACCOUNT_COLUMN = "customer_id"


@dataclass(frozen=True)
class ScopeSql:
    sql: str
    params: dict[str, Any]


def translate_crm_account_scope_to_sql(
    filters: list[dict[str, Any]] | None,
    merge: str,
    *,
    main_alias: str = LOCAL_CONTACTS_TABLE,
    id_column: str = LOCAL_CONTACTS_ACCOUNT_COLUMN,
) -> ScopeSql:
    """将 ``entity=crm_account`` 的 data-scope filters 译为联系人列表权限 WHERE。"""
    if not filters:
        return _deny()

    for item in filters:
        if _source(item) == "global" and _get_bool(item, "enabled"):
            return _allow_all()

    parts: list[str] = []
    params: dict[str, Any] = {"perm_entity_type": CRM_ENTITY_ACCOUNT}
    idx = 0

    for item in filters:
        src = _source(item)
        if src == "crm_data_authority":
            crm_id = _get_str(item, "crmId", "crm_id")
            user_id = _get_str(item, "userId", "user_id")
            if crm_id is not None:
                key = f"perm_crm_id_{idx}"
                parts.append(_mirror_exists(main_alias, id_column, f"d.crm_id = :{key}"))
                params[key] = crm_id
                idx += 1
            elif user_id is not None:
                key = f"perm_user_id_{idx}"
                parts.append(_mirror_exists(main_alias, id_column, f"d.user_id = :{key}"))
                params[key] = user_id
                idx += 1
        elif src == "org_scope" and _get_bool(item, "mirrorMatch", "mirror_match"):
            org_ids = _get_str_list(item, "crmUserIds", "crm_user_ids")
            if org_ids:
                placeholders = ", ".join(f":perm_org_id_{idx}_{i}" for i in range(len(org_ids)))
                parts.append(
                    _mirror_exists(main_alias, id_column, f"d.crm_id IN ({placeholders})")
                )
                for i, org_id in enumerate(org_ids):
                    params[f"perm_org_id_{idx}_{i}"] = org_id
                idx += 1
        elif src == "high_seas" and _get_bool(item, "enabled"):
            # local_contacts 无负责人字段，经 crm_accounts 判断公海
            parts.append(
                f"""EXISTS (
  SELECT 1 FROM crm_accounts a
  WHERE a.unique_id = {main_alias}.{id_column}
    AND a.person_in_charge_id IS NULL
)"""
            )
        elif src == "customer_attribute_unrestricted":
            attributes = _get_str_list(item, "values")
            if attributes:
                placeholders = ", ".join(
                    f":perm_customer_attribute_{idx}_{i}"
                    for i in range(len(attributes))
                )
                parts.append(
                    f"""EXISTS (
  SELECT 1 FROM crm_accounts a
  WHERE a.unique_id = {main_alias}.{id_column}
    AND a.customer_attribute IN ({placeholders})
)"""
                )
                for i, attribute in enumerate(attributes):
                    params[f"perm_customer_attribute_{idx}_{i}"] = attribute
                idx += 1

    if not parts:
        return _deny()

    joiner = " AND " if str(merge or "OR").upper() == "AND" else " OR "
    return ScopeSql(sql=f"({joiner.join(parts)})", params=params)


def _mirror_exists(main_alias: str, id_column: str, grant_match: str) -> str:
    return f"""EXISTS (
  SELECT 1 FROM {MIRROR_TABLE} d
  WHERE d.data_id = {main_alias}.{id_column}
    AND d.type = :perm_entity_type
    AND d.delete_flag = {DELETE_FLAG_NOT_DELETED}
    AND {grant_match}
)"""


def _allow_all() -> ScopeSql:
    return ScopeSql(sql="1=1", params={})


def _deny() -> ScopeSql:
    return ScopeSql(sql="1=0", params={})


def _source(item: dict[str, Any]) -> str:
    return _get_str(item, "source") or ""


def _get_str(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None:
            return str(value)
    return None


def _get_bool(item: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = item.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.lower() == "true":
            return True
    return False


def _get_str_list(item: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
    return []
