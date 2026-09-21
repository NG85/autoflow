"""只读 Aldebaran crm_entity_insight：拜访复盘（entity_type=VISIT）。

同一拜访两条结果：
- POSTVISIT_REVIEW_SALES_VIEW 销售视角
- POSTVISIT_REVIEW_LEADER_VIEW 上级视角

表由 Aldebaran 落库，Autoflow 只 SELECT。查询失败不影响推卡主流程。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VISIT_INSIGHT_ENTITY_TYPE = "VISIT"
VISIT_INSIGHT_SALES_TYPE = "POSTVISIT_REVIEW_SALES_VIEW"
VISIT_INSIGHT_LEADER_TYPE = "POSTVISIT_REVIEW_LEADER_VIEW"
# 兼容旧常量名
VISIT_INSIGHT_TYPE = VISIT_INSIGHT_SALES_TYPE

RECAP_VIEW_SALES = "sales"
RECAP_VIEW_LEADER = "leader"
_SALES_RECIPIENT_TYPES = frozenset({"recorder", "collaborative_participant"})


@dataclass(frozen=True)
class VisitRecordInsight:
    unique_id: str
    entity_id: str
    insight_type: str
    title: str
    summary: str
    detail_text: str
    category: str
    severity: str
    generated_at: Optional[datetime]

    def recap_text(self) -> str:
        return next((part for part in (self.summary, self.title, self.detail_text) if part), "")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "unique_id": self.unique_id,
            "insight_type": self.insight_type,
            "title": self.title or None,
            "summary": self.summary or None,
            "detail_text": self.detail_text or None,
            "category": self.category or None,
            "severity": self.severity or None,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }


def recap_view_for_role(role: Optional[str], *, is_group: bool = False) -> str:
    """个人：记录人/协同人看销售视角，上级/抄送看 leader 视角；群一律 leader。

    同一人兼有 leader 与协同人身份时，推送侧按角色优先级取 leader，本函数不会单独看到协同人。
    """
    if is_group:
        return RECAP_VIEW_LEADER
    if (role or "") in _SALES_RECIPIENT_TYPES:
        return RECAP_VIEW_SALES
    return RECAP_VIEW_LEADER


def _identity_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            tokens.update(_identity_tokens(*value))
            continue
        text = str(value).strip().lower()
        if not text:
            continue
        tokens.add(text)
        compact = text.replace("-", "")
        if compact:
            tokens.add(compact)
    return tokens


def collect_profile_open_ids(profile: Any) -> list[str]:
    """当前用户 oauth_accounts.open_id，用于对齐汇报链 leader。"""
    ids: list[str] = []
    seen: set[str] = set()
    for account in getattr(profile, "oauth_users", None) or []:
        open_id = str(getattr(account, "open_id", None) or "").strip()
        if open_id and open_id not in seen:
            seen.add(open_id)
            ids.append(open_id)
    return ids


def viewer_is_recorder(
    viewer_user_id: Any,
    recorder_id: Any,
) -> bool:
    """记录人 ID 按 UUID 连字符有无都视为同一人。"""
    viewer_tokens = _identity_tokens(viewer_user_id)
    recorder_tokens = _identity_tokens(recorder_id)
    return bool(viewer_tokens and recorder_tokens and viewer_tokens & recorder_tokens)


def viewer_is_leader_of_recorder(
    *,
    viewer_user_id: Any = None,
    viewer_oauth_user_id: Optional[str] = None,
    viewer_open_ids: Optional[Sequence[str]] = None,
    recorder_direct_manager_id: Optional[str] = None,
    reporting_chain_leader_open_ids: Optional[Sequence[str]] = None,
) -> bool:
    """当前用户是否是本条拜访记录人的上级。

    与推送一致：优先 OAuth 汇报链 max_levels=1（open_id）。
    汇报链有结果时不再用直属上级；链为空或查询失败时才退回 direct_manager_id。
    """
    leader_open_ids = {
        str(oid).strip().lower()
        for oid in (reporting_chain_leader_open_ids or ())
        if oid and str(oid).strip()
    }
    viewer_open = {
        str(oid).strip().lower()
        for oid in (viewer_open_ids or ())
        if oid and str(oid).strip()
    }
    if leader_open_ids:
        return bool(viewer_open and leader_open_ids & viewer_open)

    viewer_tokens = _identity_tokens(viewer_user_id, viewer_oauth_user_id, viewer_open_ids)
    return bool(viewer_tokens & _identity_tokens(recorder_direct_manager_id))


def recap_view_for_viewer(
    *,
    viewer_user_id: Any,
    recorder_id: Any = None,
    viewer_oauth_user_id: Optional[str] = None,
    collaborative_participants: Any = None,
    is_leader: bool = False,
) -> str:
    """详情页按当前用户选视角。

    记录人本人始终 sales；本条汇报上级（含同时是协同人）走 leader；
    其余协同人 sales；其他人 leader。

    collaborative_participants 必须是库内原始 JSON/列表（含 ask_id/user_id），
    不能用详情接口里拼好的姓名串。
    """
    if viewer_is_recorder(viewer_user_id, recorder_id):
        return RECAP_VIEW_SALES
    if is_leader:
        return RECAP_VIEW_LEADER

    from app.utils.participants_utils import parse_collaborative_participants_list

    viewer_tokens = _identity_tokens(viewer_user_id, viewer_oauth_user_id)
    for participant in parse_collaborative_participants_list(collaborative_participants):
        if not isinstance(participant, dict):
            continue
        participant_tokens = _identity_tokens(
            participant.get("ask_id"),
            participant.get("user_id"),
            participant.get("id"),
        )
        if viewer_tokens and participant_tokens and viewer_tokens & participant_tokens:
            return RECAP_VIEW_SALES
    return RECAP_VIEW_LEADER


def sales_insight_type() -> str:
    raw = (getattr(settings, "ALDEBARAN_VISIT_INSIGHT_SALES_TYPE", None) or VISIT_INSIGHT_SALES_TYPE).strip()
    return raw or VISIT_INSIGHT_SALES_TYPE


def leader_insight_type() -> str:
    raw = (getattr(settings, "ALDEBARAN_VISIT_INSIGHT_LEADER_TYPE", None) or VISIT_INSIGHT_LEADER_TYPE).strip()
    return raw or VISIT_INSIGHT_LEADER_TYPE


def insight_type_for_view(view: str) -> str:
    if view == RECAP_VIEW_SALES:
        return sales_insight_type()
    return leader_insight_type()


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


def _row_to_insight(row: Mapping[str, Any]) -> VisitRecordInsight:
    generated_at = row.get("generated_at")
    if generated_at is not None and not isinstance(generated_at, datetime):
        generated_at = None
    return VisitRecordInsight(
        unique_id=_text(row.get("unique_id")),
        entity_id=_text(row.get("entity_id")),
        insight_type=_text(row.get("insight_type")),
        title=_text(row.get("title")),
        summary=_text(row.get("summary")),
        detail_text=_text(row.get("detail_text")),
        category=_text(row.get("category")),
        severity=_text(row.get("severity")),
        generated_at=generated_at,
    )


def pick_visit_record_insight(
    rows: Sequence[Mapping[str, Any]],
    *,
    preferred_types: Optional[Sequence[str]] = None,
) -> Optional[VisitRecordInsight]:
    """从已按 generated_at DESC 排好的行里挑一条。指定 preferred_types 时只匹配这些类型，不串到另一视角。"""
    insights = [_row_to_insight(row) for row in rows]
    if not insights:
        return None
    preferred = tuple(
        item.strip() for item in (preferred_types or ())
        if item and str(item).strip()
    )
    if preferred:
        preferred_upper = {item.upper() for item in preferred}
        for insight in insights:
            if insight.insight_type.upper() in preferred_upper:
                return insight
        return None
    for insight in insights:
        if insight.summary:
            return insight
    return insights[0]


def _query_visit_insight_rows(session: Session, record_id: str) -> Optional[list[Mapping[str, Any]]]:
    entity_type = (
        getattr(settings, "ALDEBARAN_VISIT_INSIGHT_ENTITY_TYPE", None) or VISIT_INSIGHT_ENTITY_TYPE
    ).strip() or VISIT_INSIGHT_ENTITY_TYPE
    table_name = (getattr(settings, "ALDEBARAN_ENTITY_INSIGHT_TABLE", None) or "crm_entity_insight").strip()
    try:
        table_sql = _quote_ident(table_name)
    except ValueError:
        logger.warning("Skip visit insight read: invalid table name %r", table_name)
        return None

    sql = text(
        f"""
        SELECT
            unique_id,
            entity_id,
            insight_type,
            title,
            summary,
            detail_text,
            category,
            severity,
            generated_at
        FROM {table_sql}
        WHERE entity_type = :entity_type
          AND entity_id = :entity_id
          AND is_deleted = 0
          AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP())
        ORDER BY generated_at DESC, id DESC
        LIMIT 20
        """
    )
    try:
        result = session.execute(
            sql,
            {"entity_type": entity_type, "entity_id": record_id},
        )
        return list(result.mappings().all())
    except Exception as exc:
        logger.warning(
            "Failed to load visit entity insight, record_id=%s table=%s: %s",
            record_id,
            table_name,
            exc,
        )
        return None


def load_visit_record_insight(
    session: Optional[Session],
    record_id: str,
    *,
    preferred_types: Optional[Sequence[str]] = None,
    view: Optional[str] = None,
) -> Optional[VisitRecordInsight]:
    """读取拜访一条未删除、未过期洞察。view=sales/leader 时按对应 insight_type。"""
    rid = (record_id or "").strip()
    if session is None or not rid:
        return None
    rows = _query_visit_insight_rows(session, rid)
    if rows is None:
        return None
    types = preferred_types
    if types is None and view:
        types = (insight_type_for_view(view),)
    if types is None:
        types = (sales_insight_type(),)
    picked = pick_visit_record_insight(rows, preferred_types=types)
    if picked is None:
        logger.info(
            "No visit entity insight found, record_id=%s view=%s types=%s",
            rid,
            view,
            types,
        )
    return picked


def load_visit_record_insights_by_view(
    session: Optional[Session],
    record_id: str,
) -> dict[str, Optional[VisitRecordInsight]]:
    """一次查出同一拜访的销售/上级两条复盘。"""
    empty: dict[str, Optional[VisitRecordInsight]] = {
        RECAP_VIEW_SALES: None,
        RECAP_VIEW_LEADER: None,
    }
    rid = (record_id or "").strip()
    if session is None or not rid:
        return empty
    rows = _query_visit_insight_rows(session, rid)
    if not rows:
        return empty
    return {
        RECAP_VIEW_SALES: pick_visit_record_insight(rows, preferred_types=(sales_insight_type(),)),
        RECAP_VIEW_LEADER: pick_visit_record_insight(rows, preferred_types=(leader_insight_type(),)),
    }


def has_visit_recap_insight(
    insights: Mapping[str, Optional[VisitRecordInsight]],
) -> bool:
    return bool(insights.get(RECAP_VIEW_SALES) or insights.get(RECAP_VIEW_LEADER))


def resolve_visit_record_detail_recap(
    insights: Mapping[str, Optional[VisitRecordInsight]],
    *,
    viewer_is_recorder: bool,
    resolve_non_recorder_view: Optional[Callable[[], str]] = None,
) -> Optional[tuple[str, Optional[VisitRecordInsight]]]:
    """详情装配：没有复盘就不选视角（调用方也不该去打汇报链）。

    有复盘时记录人直接 sales；其他人由 resolve_non_recorder_view 决定。
    """
    if not has_visit_recap_insight(insights):
        return None
    if viewer_is_recorder:
        view = RECAP_VIEW_SALES
    else:
        view = (resolve_non_recorder_view() if resolve_non_recorder_view else None) or RECAP_VIEW_LEADER
    if view not in (RECAP_VIEW_SALES, RECAP_VIEW_LEADER):
        view = RECAP_VIEW_LEADER
    return view, insights.get(view)
