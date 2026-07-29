"""将 OAuth data-scope（entity=crm_opportunity）译为商机列表 SQL WHERE。

商机**不继承**客户权限；无 high_seas / customer_attribute_unrestricted。
真源：aptsell-oauth docs/examples/crm_translate_scope_to_sql.py、api-permission-data-scope.md。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CRM_ENTITY_OPPORTUNITY = "crm_opportunity"
MIRROR_TABLE = "crm_data_authority"
DELETE_FLAG_NOT_DELETED = "0"
CRM_OPPORTUNITIES_TABLE = "crm_opportunities"
CRM_OPPORTUNITIES_ID_COLUMN = "unique_id"


@dataclass(frozen=True)
class ScopeSql:
    sql: str
    params: dict[str, Any]


def translate_crm_opportunity_scope_to_sql(
    filters: list[dict[str, Any]] | None,
    merge: str,
    *,
    main_alias: str = CRM_OPPORTUNITIES_TABLE,
    id_column: str = CRM_OPPORTUNITIES_ID_COLUMN,
) -> ScopeSql:
    """将 ``entity=crm_opportunity`` 的 data-scope filters 译为商机列表权限 WHERE。"""
    if not filters:
        return _deny()

    for item in filters:
        if _source(item) == "global" and _get_bool(item, "enabled"):
            return _allow_all()

    parts: list[str] = []
    params: dict[str, Any] = {"perm_entity_type": CRM_ENTITY_OPPORTUNITY}
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
        # high_seas / customer_attribute_unrestricted：仅 crm_account，商机忽略

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
