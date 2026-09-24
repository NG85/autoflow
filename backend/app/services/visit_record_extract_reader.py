"""只读 Aldebaran crm_postvisit_extract_items：销售视角复盘结构化抽取。

表由 Aldebaran 落库，Autoflow 只 SELECT。查询失败返回空结果，不抬 500。
card_links 以抽取行上的 YAML 快照为准；CARD_LINKS_BY_PROFILE 仅作无快照时的兜底。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

EXTRACT_STATUS_SUPERSEDED = "SUPERSEDED"
VIEWER_ROLE_SALES = "sales"
DEFAULT_PROFILE_ID = "postvisit_sales_v1_0"

# 仅当行上没有 card_links 快照时使用（历史数据 / 列尚未加上）。
# 新抽取以 Aldebaran sales_extract.yaml 落库快照为准，不要在这里改入口。
CARD_LINKS_BY_PROFILE: dict[str, list[dict[str, Any]]] = {
    DEFAULT_PROFILE_ID: [
        {
            "key": "follow_ups",
            "title": "待我跟进",
            "item_types": ["SUGGESTED_ACTION"],
            "show_if_any": True,
        },
        {
            "key": "next_visit",
            "title": "下次见面",
            "item_types": ["NEXT_VISIT_PLAN"],
            "show_if_any": True,
        },
        {
            "key": "key_issues",
            "title": "当前最关键问题",
            "item_types": ["KEY_GAP", "RISK"],
            "require_any": ["KEY_GAP", "RISK"],
        },
        {
            "key": "potential_opps",
            "title": "潜在新商机",
            "item_types": ["POTENTIAL_OPP"],
            "show_if_any": True,
        },
    ]
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _quote_ident(name: str) -> str:
    raw = (name or "").strip()
    if not _IDENT_RE.fullmatch(raw):
        raise ValueError(f"invalid sql identifier: {name!r}")
    return f"`{raw}`"


def _parse_json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _parse_payload(value: Any) -> dict[str, Any]:
    parsed = _parse_json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _parse_card_link_specs(value: Any) -> list[dict[str, Any]]:
    parsed = _parse_json_value(value)
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("key")]


def resolve_card_link_specs(
    rows: Sequence[Mapping[str, Any]],
    profile_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    for row in rows:
        specs = _parse_card_link_specs(row.get("card_links"))
        if specs:
            return specs
    return card_links_for_profile(profile_id)


def serialize_extract_item(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = _parse_payload(row.get("payload"))
    reserved = {"claim", "why", "evidence", "confidence"}
    extras = {k: v for k, v in payload.items() if k not in reserved}
    data: dict[str, Any] = {
        "unique_id": _text(row.get("unique_id")),
        "item_type": _text(row.get("item_type")),
        "extract_key": _text(row.get("extract_key")),
        "claim": payload.get("claim"),
        "why": payload.get("why"),
        "evidence": payload.get("evidence") or [],
        "confidence": payload.get("confidence"),
        "severity": _text(row.get("severity")) or None,
        "status": _text(row.get("status")),
    }
    data.update(extras)
    return data


def _action_sort_key(item: dict[str, Any]) -> tuple:
    origin = str(item.get("origin") or "ai_recommendation").strip().lower()
    origin_rank = 0 if origin != "ai_recommendation" else 1
    due_rank = 0 if item.get("due_hint") else 1
    priority = str(item.get("priority") or "").upper()
    p_rank = 0 if priority == "P0" else 1
    confidence = float(item.get("confidence") or 0)
    return (origin_rank, due_rank, p_rank, -confidence)


def group_extract_items(serialized: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in serialized:
        grouped.setdefault(item["item_type"], []).append(item)
    for item_type, rows in grouped.items():
        if item_type == "SUGGESTED_ACTION":
            rows.sort(key=_action_sort_key)
        else:
            rows.sort(key=lambda row: float(row.get("confidence") or 0), reverse=True)
    return grouped


def card_links_for_profile(profile_id: Optional[str]) -> list[dict[str, Any]]:
    pid = (profile_id or "").strip() or DEFAULT_PROFILE_ID
    return CARD_LINKS_BY_PROFILE.get(pid) or CARD_LINKS_BY_PROFILE[DEFAULT_PROFILE_ID]


def build_card_links(
    grouped: dict[str, list[dict[str, Any]]],
    specs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for spec in specs:
        types = [str(t) for t in (spec.get("item_types") or [])]
        require_any = [str(t) for t in (spec.get("require_any") or [])]
        collected: list[dict[str, Any]] = []
        for item_type in types:
            collected.extend(grouped.get(item_type) or [])
        if require_any:
            if not any(grouped.get(item_type) for item_type in require_any):
                continue
        elif spec.get("show_if_any", True) and not collected:
            continue
        preview_pool = collected
        if require_any:
            preview_pool = []
            for item_type in require_any:
                preview_pool.extend(grouped.get(item_type) or [])
            if not preview_pool:
                preview_pool = collected
        preview = preview_pool[0].get("claim") if preview_pool else None
        links.append(
            {
                "key": spec.get("key"),
                "title": spec.get("title"),
                "count": len(collected),
                "preview": preview,
                "item_types": types,
            }
        )
    return links


def empty_extract_response(visit_id: str, profile_id: str = DEFAULT_PROFILE_ID) -> dict[str, Any]:
    return {
        "visit_id": visit_id,
        "profile_id": profile_id,
        "items": {},
        "card_links": [],
    }


def build_extract_response(
    *,
    visit_id: str,
    rows: Sequence[Mapping[str, Any]],
    profile_id: Optional[str] = None,
) -> dict[str, Any]:
    serialized = [serialize_extract_item(row) for row in rows]
    grouped = group_extract_items(serialized)
    pid = profile_id or DEFAULT_PROFILE_ID
    if rows:
        pid = _text(rows[0].get("profile_id")) or pid
    return {
        "visit_id": visit_id,
        "profile_id": pid,
        "items": grouped,
        "card_links": build_card_links(grouped, resolve_card_link_specs(rows, pid)),
    }


def _query_extract_rows(
    session: Session,
    visit_id: str,
    viewer_role: str = VIEWER_ROLE_SALES,
) -> Optional[list[Mapping[str, Any]]]:
    table_name = (
        getattr(settings, "ALDEBARAN_POSTVISIT_EXTRACT_TABLE", None)
        or "crm_postvisit_extract_items"
    ).strip()
    try:
        table_sql = _quote_ident(table_name)
    except ValueError:
        logger.warning("Skip visit extract read: invalid table name %r", table_name)
        return None

    sql = text(
        f"""
        SELECT
            unique_id,
            visit_id,
            item_type,
            extract_key,
            payload,
            severity,
            profile_id,
            card_links,
            status
        FROM {table_sql}
        WHERE visit_id = :visit_id
          AND viewer_role = :viewer_role
          AND is_deleted = 0
          AND status <> :superseded
        """
    )
    try:
        result = session.execute(
            sql,
            {
                "visit_id": visit_id,
                "viewer_role": viewer_role,
                "superseded": EXTRACT_STATUS_SUPERSEDED,
            },
        )
        return list(result.mappings().all())
    except Exception as exc:
        logger.warning(
            "Failed to load visit extract items, visit_id=%s table=%s: %s",
            visit_id,
            table_name,
            exc,
        )
        return None


def load_visit_record_extract_response(
    session: Optional[Session],
    record_id: str,
    *,
    viewer_role: str = VIEWER_ROLE_SALES,
) -> dict[str, Any]:
    """读取拜访销售视角有效抽取条目，组装 items + card_links。"""
    rid = (record_id or "").strip()
    empty = empty_extract_response(rid)
    if session is None or not rid:
        return empty
    rows = _query_extract_rows(session, rid, viewer_role=viewer_role)
    if not rows:
        return empty
    return build_extract_response(visit_id=rid, rows=rows)
