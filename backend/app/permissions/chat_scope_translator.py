"""将 OAuth data-scope 译为 chats 列表（GET /chats）的可见范围。

真源：aptsell-oauth docs/api-permission-data-scope.md § W4 P1.1（业务原生实体列表）、
docs/data-scope-matrix.md §3.2（sia问答历史 / 拜访攻略历史）。

GET /chats 按 chat_type 映射 OAuth data-scope 实体（会话历史）：
- ChatType.DEFAULT             → enablement_sia_history
- ChatType.CLIENT_VISIT_GUIDE  → enablement_visit_guide_history

``chats`` 表仅有 ``user_id``（owner，``users.id``），无 crm_user_id / 关联 CRM 列，故：
- self_only / self_owner / self_creator → chats.user_id = 当前用户
- org_scope（team_subordinates）        → chats.user_id IN 辖区 users.id（含本人 + 下属）
- global                                → 放开全部（BIZ_ADMIN / EXECUTIVE / SYS_ADMIN）
- collaborator / linked_crm             → chats 无对应结构，跳过

历史实体 scope 仅含 SELF_ONLY / ORG_TEAM_SUB / GLOBAL / BIZ_SUPPORT_SCOPE，
不出现 collaborator / linked_crm；此处保留跳过分支以兼容未来变更。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# self 语义 filter（历史实体主要为 self_only；兼容其余自身范围来源）
_SELF_SOURCES = frozenset({"self_only", "self_owner", "self_creator"})


@dataclass(frozen=True)
class ChatScopeResult:
    """chats 列表可见范围判定结果。

    - allow_all=True：放开全部（global 角色），列表不加 owner 限制
    - deny=True：无任何可见范围，列表应返回空
    - 否则按 owner_user_ids（users.id 字符串）过滤 ``chats.user_id IN (...)``
    """

    allow_all: bool = False
    deny: bool = False
    owner_user_ids: tuple[str, ...] = field(default_factory=tuple)


def _source(item: dict[str, Any]) -> str:
    return (item.get("source") or "").strip()


def _get_bool(item: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in item:
            return bool(item[key])
    return False


def translate_chat_scope(
    filters: list[dict[str, Any]] | None,
    *,
    user_id: str,
    org_scope_user_ids: list[str] | None = None,
) -> ChatScopeResult:
    """将 data-scope filters 译为 chats owner 可见集合。

    Args:
        filters: ``POST /permission/data-scope`` 返回的 ``result.filters``
        user_id: 当前登录用户 ``users.id``（self 范围命中本人）
        org_scope_user_ids: ``map_org_scope_from_filters`` 展开后的 users.id 列表
    """
    if not filters:
        return ChatScopeResult(deny=True)

    for item in filters:
        if _source(item) == "global" and _get_bool(item, "enabled"):
            return ChatScopeResult(allow_all=True)

    owner_user_ids: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        candidate = str(value or "").strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            owner_user_ids.append(candidate)

    has_self = any(_source(item) in _SELF_SOURCES for item in filters)
    if has_self:
        _add(user_id)

    for uid in org_scope_user_ids or []:
        _add(uid)

    if not owner_user_ids:
        return ChatScopeResult(deny=True)

    return ChatScopeResult(owner_user_ids=tuple(owner_user_ids))
