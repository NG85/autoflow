"""只读 Aldebaran crm_entity_insight：拜访复盘（entity_type=VISIT）。

经营复盘：
- POSTVISIT_REVIEW 现行统一报告，销售与上级看同一份
- POSTVISIT_REVIEW_SALES_VIEW / POSTVISIT_REVIEW_LEAD_VIEW 历史双视角，没有统一报告时才用

表现评价仍分视角：
- VISIT_SALES_PERFORMANCE_SALES_VIEW
- VISIT_SALES_PERFORMANCE_LEADER_VIEW

通过 CRMEntityInsight 查询。查询失败不影响推卡主流程。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.models.crm_entity_insight import CRMEntityInsight

logger = logging.getLogger(__name__)

VISIT_INSIGHT_ENTITY_TYPE = "VISIT"
VISIT_INSIGHT_UNIFIED_TYPE = "POSTVISIT_REVIEW"
VISIT_INSIGHT_SALES_TYPE = "POSTVISIT_REVIEW_SALES_VIEW"
VISIT_INSIGHT_LEADER_TYPE = "POSTVISIT_REVIEW_LEAD_VIEW"
VISIT_INSIGHT_PERFORMANCE_SALES_TYPE = "VISIT_SALES_PERFORMANCE_SALES_VIEW"
VISIT_INSIGHT_PERFORMANCE_LEADER_TYPE = "VISIT_SALES_PERFORMANCE_LEADER_VIEW"
# 兼容旧常量名。统计「销售侧经营复盘」时同时计入统一报告和历史销售视角。
VISIT_INSIGHT_TYPE = VISIT_INSIGHT_SALES_TYPE
SALES_BUSINESS_RECAP_TYPES = (
    VISIT_INSIGHT_UNIFIED_TYPE,
    VISIT_INSIGHT_SALES_TYPE,
)

RECAP_VIEW_SALES = "sales"
RECAP_VIEW_LEADER = "leader"
PERFORMANCE_VIEW_SALES = "performance_sales"
PERFORMANCE_VIEW_LEADER = "performance_leader"
RECAP_TAB_BUSINESS = "business"
RECAP_TAB_PERFORMANCE = "performance"
RECAP_PAGE_TABS = (
    (RECAP_TAB_BUSINESS, "业务推进"),
    (RECAP_TAB_PERFORMANCE, "销售表现"),
)
_SALES_RECIPIENT_TYPES = frozenset({"recorder", "collaborative_participant"})
_VISIT_PAGE_INSIGHT_TYPES = (
    VISIT_INSIGHT_UNIFIED_TYPE,
    VISIT_INSIGHT_SALES_TYPE,
    VISIT_INSIGHT_LEADER_TYPE,
    VISIT_INSIGHT_PERFORMANCE_SALES_TYPE,
    VISIT_INSIGHT_PERFORMANCE_LEADER_TYPE,
)


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


def insight_type_for_view(view: str) -> str:
    """历史双视角类型。现行统一报告不按 view 拆开。"""
    if view == RECAP_VIEW_SALES:
        return VISIT_INSIGHT_SALES_TYPE
    return VISIT_INSIGHT_LEADER_TYPE


def pick_business_insight(
    rows: Sequence[Mapping[str, Any]],
    *,
    fallback_type: str,
) -> Optional[VisitRecordInsight]:
    """经营复盘：有统一报告就用最新一条，否则用该视角的历史报告。"""
    unified = pick_visit_record_insight(
        rows,
        preferred_types=(VISIT_INSIGHT_UNIFIED_TYPE,),
    )
    if unified is not None:
        return unified
    return pick_visit_record_insight(rows, preferred_types=(fallback_type,))


def performance_insight_type_for_view(view: str) -> str:
    if view == RECAP_VIEW_SALES:
        return VISIT_INSIGHT_PERFORMANCE_SALES_TYPE
    return VISIT_INSIGHT_PERFORMANCE_LEADER_TYPE


def performance_view_for_recap_view(view: str) -> str:
    if view == RECAP_VIEW_SALES:
        return PERFORMANCE_VIEW_SALES
    return PERFORMANCE_VIEW_LEADER


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


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


def _as_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return row.model_dump()


def _query_visit_insight_rows(session: Session, record_id: str) -> Optional[list[Mapping[str, Any]]]:
    statement = (
        select(CRMEntityInsight)
        .where(CRMEntityInsight.entity_type == VISIT_INSIGHT_ENTITY_TYPE)
        .where(CRMEntityInsight.entity_id == record_id)
        .where(CRMEntityInsight.is_deleted == 0)
        .where(
            or_(
                CRMEntityInsight.expires_at.is_(None),
                CRMEntityInsight.expires_at > func.utc_timestamp(),
            )
        )
        .order_by(col(CRMEntityInsight.generated_at).desc(), col(CRMEntityInsight.id).desc())
        .limit(20)
    )
    try:
        rows = session.exec(statement).all()
        return [_as_mapping(row) for row in rows]
    except Exception as exc:
        logger.warning(
            "Failed to load visit entity insight, record_id=%s: %s",
            record_id,
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
    """读取拜访一条未删除、未过期洞察。

    view=sales/leader 时优先统一报告，没有再退回该视角的历史类型。
    显式 preferred_types 仍按传入类型挑选，不套经营复盘优先级。
    """
    rid = (record_id or "").strip()
    if session is None or not rid:
        return None
    rows = _query_visit_insight_rows(session, rid)
    if rows is None:
        return None
    if preferred_types is not None:
        picked = pick_visit_record_insight(rows, preferred_types=preferred_types)
    else:
        fallback = insight_type_for_view(view) if view else VISIT_INSIGHT_SALES_TYPE
        picked = pick_business_insight(rows, fallback_type=fallback)
    if picked is None:
        logger.info(
            "No visit entity insight found, record_id=%s view=%s preferred=%s",
            rid,
            view,
            preferred_types,
        )
    return picked


def _empty_visit_insights() -> dict[str, Optional[VisitRecordInsight]]:
    return {
        RECAP_VIEW_SALES: None,
        RECAP_VIEW_LEADER: None,
        PERFORMANCE_VIEW_SALES: None,
        PERFORMANCE_VIEW_LEADER: None,
    }


def load_visit_record_insights_by_view(
    session: Optional[Session],
    record_id: str,
) -> dict[str, Optional[VisitRecordInsight]]:
    """一次查出经营复盘和两个视角的表现评价。

    有 POSTVISIT_REVIEW 时，销售和上级槽位是同一份经营复盘。
    没有时，各自退回历史销售视角、上级视角。表现评价不合并。
    """
    empty = _empty_visit_insights()
    rid = (record_id or "").strip()
    if session is None or not rid:
        return empty
    rows = _query_visit_insight_rows(session, rid)
    if not rows:
        return empty
    return {
        RECAP_VIEW_SALES: pick_business_insight(
            rows,
            fallback_type=VISIT_INSIGHT_SALES_TYPE,
        ),
        RECAP_VIEW_LEADER: pick_business_insight(
            rows,
            fallback_type=VISIT_INSIGHT_LEADER_TYPE,
        ),
        PERFORMANCE_VIEW_SALES: pick_visit_record_insight(
            rows,
            preferred_types=(VISIT_INSIGHT_PERFORMANCE_SALES_TYPE,),
        ),
        PERFORMANCE_VIEW_LEADER: pick_visit_record_insight(
            rows,
            preferred_types=(VISIT_INSIGHT_PERFORMANCE_LEADER_TYPE,),
        ),
    }


def has_visit_recap_insight(
    insights: Mapping[str, Optional[VisitRecordInsight]],
) -> bool:
    return bool(insights.get(RECAP_VIEW_SALES) or insights.get(RECAP_VIEW_LEADER))


def has_visit_recap_page_insight(
    insights: Mapping[str, Optional[VisitRecordInsight]],
) -> bool:
    """复盘或表现评价至少有一条时，复盘页可以按视角打开。"""
    return bool(
        has_visit_recap_insight(insights)
        or insights.get(PERFORMANCE_VIEW_SALES)
        or insights.get(PERFORMANCE_VIEW_LEADER)
    )


def viewer_has_visible_recap(
    insight_types: Sequence[str],
    *,
    is_recorder: bool = False,
    is_reporting_leader: bool = False,
    is_sys_admin: bool = False,
    is_collaborative_participant: bool = False,
) -> bool:
    """当前用户打开复盘页时，是否有他能看的业务推进或人员表现评价。

    调用方已经能打开页面。记录人和协同人看销售视角的两份。
    其他人看上级视角的业务推进。上级视角的人员表现评价只在直接汇报上级或公司级管理人员时算进去。
    """
    present = {
        str(item).strip().upper()
        for item in (insight_types or ())
        if item and str(item).strip()
    }
    if not present:
        return False
    if VISIT_INSIGHT_UNIFIED_TYPE.upper() in present:
        return True
    sales_types = {
        VISIT_INSIGHT_SALES_TYPE.upper(),
        VISIT_INSIGHT_PERFORMANCE_SALES_TYPE.upper(),
    }
    if is_recorder or is_collaborative_participant:
        if present & sales_types:
            return True
        if is_recorder:
            return False
    if VISIT_INSIGHT_LEADER_TYPE.upper() in present:
        return True
    return bool(
        (is_reporting_leader or is_sys_admin)
        and VISIT_INSIGHT_PERFORMANCE_LEADER_TYPE.upper() in present
    )


def load_visit_recap_types_by_record_id(
    session: Optional[Session],
    record_ids: Sequence[str],
) -> dict[str, set[str]]:
    """一批拜访上未删除、未过期的复盘和表现评价类型。查询失败时全部视为没有。"""
    ids = list(dict.fromkeys(_text(item) for item in record_ids if _text(item)))
    if session is None or not ids:
        return {}
    statement = (
        select(CRMEntityInsight.entity_id, CRMEntityInsight.insight_type)
        .where(CRMEntityInsight.entity_type == VISIT_INSIGHT_ENTITY_TYPE)
        .where(CRMEntityInsight.entity_id.in_(ids))
        .where(
            CRMEntityInsight.insight_type.in_(_VISIT_PAGE_INSIGHT_TYPES)
        )
        .where(CRMEntityInsight.is_deleted == 0)
        .where(
            or_(
                CRMEntityInsight.expires_at.is_(None),
                CRMEntityInsight.expires_at > func.utc_timestamp(),
            )
        )
    )
    try:
        rows = session.exec(statement).all()
    except Exception as exc:
        logger.warning("批量读取拜访复盘标记失败: %s", exc)
        return {}
    found: dict[str, set[str]] = {}
    for row in rows:
        entity_id = _text(row[0] if not isinstance(row, Mapping) else row.get("entity_id"))
        insight_type = _text(row[1] if not isinstance(row, Mapping) else row.get("insight_type"))
        if not entity_id or not insight_type:
            continue
        found.setdefault(entity_id, set()).add(insight_type)
    return found


def resolve_visit_record_page(
    insights: Mapping[str, Optional[VisitRecordInsight]],
    *,
    view: Optional[str] = None,
    resolve_view: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[tuple[str, Optional[VisitRecordInsight], Optional[VisitRecordInsight]]]:
    """没有复盘也没有表现评价，或当前用户没有可看的视角时，返回 None。

    返回 (视角, 该视角复盘, 该视角表现评价)。缺的那条是 None，另一条仍可看。
    """
    if not has_visit_recap_page_insight(insights):
        return None
    chosen = view if view is not None else (resolve_view() if resolve_view else None)
    if chosen not in (RECAP_VIEW_SALES, RECAP_VIEW_LEADER):
        return None
    return (
        chosen,
        insights.get(chosen),
        insights.get(performance_view_for_recap_view(chosen)),
    )


def resolve_visit_record_detail_recap(
    insights: Mapping[str, Optional[VisitRecordInsight]],
    *,
    view: Optional[str] = None,
    resolve_view: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[tuple[str, Optional[VisitRecordInsight]]]:
    """没有复盘，或当前用户没有可看的视角时，返回 None。"""
    if not has_visit_recap_insight(insights):
        return None
    chosen = view if view is not None else (resolve_view() if resolve_view else None)
    if chosen not in (RECAP_VIEW_SALES, RECAP_VIEW_LEADER):
        return None
    return chosen, insights.get(chosen)


def _row_entity_id(row: Any) -> str:
    if isinstance(row, Mapping):
        return _text(row.get("entity_id"))
    return _text(getattr(row, "entity_id", None))


def _row_insight_type(row: Any) -> str:
    if isinstance(row, Mapping):
        return _text(row.get("insight_type"))
    return _text(getattr(row, "insight_type", None))


def _row_generated_at(row: Any) -> datetime:
    if isinstance(row, Mapping):
        value = row.get("generated_at")
    else:
        value = getattr(row, "generated_at", None)
    if isinstance(value, datetime):
        return value
    return datetime.min


def select_one_business_recap_per_visit(
    rows: Sequence[Any],
    *,
    fallback_type: str = VISIT_INSIGHT_SALES_TYPE,
) -> list[Any]:
    """每个拜访留一条经营复盘：统一报告优先于历史视角，同类型留最新一条。"""
    grouped: dict[str, list[Any]] = {}
    for row in rows or []:
        entity_id = _row_entity_id(row)
        if entity_id:
            grouped.setdefault(entity_id, []).append(row)
    selected: list[Any] = []
    unified = VISIT_INSIGHT_UNIFIED_TYPE.upper()
    fallback = fallback_type.upper()
    for group in grouped.values():
        preferred = [row for row in group if _row_insight_type(row).upper() == unified]
        pool = preferred or [
            row for row in group if _row_insight_type(row).upper() == fallback
        ]
        if not pool:
            continue
        pool.sort(key=_row_generated_at, reverse=True)
        selected.append(pool[0])
    return selected
