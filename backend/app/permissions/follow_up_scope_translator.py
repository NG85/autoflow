"""将 OAuth data-scope（entity=follow_up）译为跟进列表 SQL WHERE 片段。

真源：aptsell-oauth docs/api-permission-data-scope.md § W4 P1.1、附录 F。
与 POST /permission/check 数据层口径一致；列表勿逐条 check。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScopeSql:
    sql: str
    params: dict[str, Any]


def _source(item: dict[str, Any]) -> str:
    return (item.get("source") or "").strip()


def _get_bool(item: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in item:
            return bool(item[key])
    return False


def _get_str_list(item: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
    return []


def _deny() -> ScopeSql:
    return ScopeSql(sql="1=0", params={})


def _allow_all() -> ScopeSql:
    return ScopeSql(sql="1=1", params={})


def _compact_user_id(value: str) -> str:
    """crm_sales_visit_records.recorder_id 存 32 位无连字符 UUID。"""
    return str(value).replace("-", "")


def _recorder_matches_user(column: str, param_key: str) -> str:
    return f"{column} = :{param_key}"


def linked_crm_follow_up_sql(
    *,
    main_alias: str,
    account_scope_sql: str,
    opportunity_scope_sql: str,
    account_params: dict[str, Any],
    opportunity_params: dict[str, Any],
) -> ScopeSql:
    """LINKED_CRM 开启时：行级商机优先，否则 account_id，否则 partner_id。"""
    opp = opportunity_scope_sql.replace("d.data_id", f"{main_alias}.opportunity_id")
    acc = account_scope_sql.replace("d.data_id", f"{main_alias}.account_id")
    partner = account_scope_sql.replace("d.data_id", f"{main_alias}.partner_id")
    sql = f"""(
  ({main_alias}.opportunity_id IS NOT NULL AND {main_alias}.opportunity_id <> '' AND ({opp}))
  OR (
    ({main_alias}.opportunity_id IS NULL OR {main_alias}.opportunity_id = '')
    AND {main_alias}.account_id IS NOT NULL AND {main_alias}.account_id <> ''
    AND ({acc})
  )
  OR (
    ({main_alias}.opportunity_id IS NULL OR {main_alias}.opportunity_id = '')
    AND ({main_alias}.account_id IS NULL OR {main_alias}.account_id = '')
    AND {main_alias}.partner_id IS NOT NULL AND {main_alias}.partner_id <> ''
    AND ({partner})
  )
)"""
    params = {**account_params, **opportunity_params}
    return ScopeSql(sql=sql, params=params)


def translate_follow_up_scope_to_sql(
    filters: list[dict[str, Any]] | None,
    merge: str,
    *,
    main_alias: str = "f",
    user_id: str,
    org_scope_user_ids: list[str] | None = None,
    collab_exists_sql: str | None = None,
    linked_crm_sql: str | None = None,
) -> ScopeSql:
    """将 follow_up data-scope filters 译为权限 WHERE 子表达式。"""
    if not filters:
        return _deny()

    for item in filters:
        if _source(item) == "global" and _get_bool(item, "enabled"):
            return _allow_all()

    parts: list[str] = []
    params: dict[str, Any] = {}

    linked_crm_enabled = False
    for item in filters:
        src = _source(item)
        if src == "self_creator":
            params["perm_user_id"] = _compact_user_id(user_id)
            parts.append(_recorder_matches_user(f"{main_alias}.recorder_id", "perm_user_id"))
        elif src == "org_scope":
            ids = list(org_scope_user_ids or [])
            if not ids and _get_str_list(item, "crmUserIds", "crm_user_ids"):
                continue
            if ids:
                placeholders = ", ".join(f":perm_org_uid_{i}" for i in range(len(ids)))
                parts.append(f"{main_alias}.recorder_id IN ({placeholders})")
                for i, uid in enumerate(ids):
                    params[f"perm_org_uid_{i}"] = _compact_user_id(uid)
        elif src == "collaborator" and collab_exists_sql:
            # 业务侧未提供 collab_exists_sql 时跳过（Autoflow 暂无 follow_up_collab）
            parts.append(f"({collab_exists_sql})")
        elif src == "linked_crm":
            linked_crm_enabled = _get_bool(item, "enabled")

    if linked_crm_enabled and linked_crm_sql:
        parts.append(f"({linked_crm_sql})")

    if not parts:
        return _deny()

    joiner = " AND " if merge.upper() == "AND" else " OR "
    return ScopeSql(sql=f"({joiner.join(parts)})", params=params)
