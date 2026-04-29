from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case
from sqlalchemy.orm import load_only
from sqlmodel import Session, select, func

from app.models.crm_review import (
    CRMReviewAttendee,
    CRMReviewOppAuditLog,
    CRMReviewOppBranchSnapshot,
    CRMReviewOppBranchSnapshotBasicOut,
    CRMReviewOppBranchSnapshotCache,
    CRMReviewOppRiskProgress,
    CRMReviewSession,
    REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS,
)
from app.models.crm_opportunities import CRMOpportunity
from app.models.crm_system_configurations import CRMSystemConfiguration
from app.repositories.crm_review_attendee import crm_review_attendee_repo
from app.repositories.crm_review_audit import crm_review_opp_audit_log_repo
from app.repositories.crm_review_branch_snapshot import crm_review_opp_branch_snapshot_cache_repo
from app.repositories.crm_review_session import crm_review_session_repo
from app.services.aldebaran_service import aldebaran_client

logger = logging.getLogger(__name__)

# Leader merge: keyset pagination on cache PK ``id`` (only orders rows within the cache table
MERGE_CACHE_BATCH_SIZE = 500


def _audit_json_default(obj: Any) -> str:
    """Best-effort JSON fallback for audit payloads.

    DB fields like `Decimal` are not JSON-serializable by default.
    We stringify them to keep audit logs durable.
    """

    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        # Keep timezone info when present.
        return obj.isoformat()
    return str(obj)


EDITABLE_FIELDS = REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS

# submit 写入 cache 的元数据；leader merge 回主表时可能与主表不一致，需一并按 cache 覆盖。
_MERGE_SUBMIT_SYNC_FIELD_NAMES: tuple[str, ...] = tuple(
    f
    for f in (
        "update_time",
        "last_modified_by_id",
        "last_modified_by",
        "was_modified",
        "modification_count",
        "initial_edit_modification_count",
        "meeting_edit_modification_count",
    )
    if f in CRMReviewOppBranchSnapshot.model_fields
    and f in CRMReviewOppBranchSnapshotCache.model_fields
)

# merge_branch_snapshots_from_cache_to_main: SELECT 列裁剪（宽表时减少 IO / ORM 物化成本）
_MERGE_CACHE_LOAD_COLUMNS: tuple[Any, ...] = (
    CRMReviewOppBranchSnapshotCache.id,
    CRMReviewOppBranchSnapshotCache.unique_id,
    CRMReviewOppBranchSnapshotCache.opportunity_id,
    CRMReviewOppBranchSnapshotCache.snapshot_period,
    *(
        getattr(CRMReviewOppBranchSnapshotCache, name)
        for name in REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS
    ),
    *(getattr(CRMReviewOppBranchSnapshotCache, name) for name in _MERGE_SUBMIT_SYNC_FIELD_NAMES),
)
_MERGE_MAIN_LOAD_COLUMNS: tuple[Any, ...] = (
    CRMReviewOppBranchSnapshot.id,
    CRMReviewOppBranchSnapshot.unique_id,
    CRMReviewOppBranchSnapshot.opportunity_id,
    CRMReviewOppBranchSnapshot.snapshot_period,
    CRMReviewOppBranchSnapshot.was_changed_to_commit,
    *(
        getattr(CRMReviewOppBranchSnapshot, name)
        for name in REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS
    ),
    *(getattr(CRMReviewOppBranchSnapshot, name) for name in _MERGE_SUBMIT_SYNC_FIELD_NAMES),
)

_FORECAST_TYPE_RANK_BY_CONFIG_KEY: Dict[str, int] = {
    "commit": 0,
    "upside": 1,
    "closed_won": 2,
}


def _parse_forecast_type_aliases_from_config_value(raw: Optional[str]) -> List[str]:
    """Parse ForecastTypeMapping.config_value (JSON array or plain string) into display aliases."""
    raw = str(raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x or "").strip()]
        if isinstance(parsed, str):
            s = parsed.strip()
            return [s] if s else []
    except (json.JSONDecodeError, TypeError):
        pass
    return [raw]


# ForecastTypeMapping 变更极少：短时缓存避免每条列表请求都打配置表（仅影响排序 CASE 的 IN 列表）。
_FORECAST_RANK_ALIASES_CACHE_TTL_SEC = 120.0
_forecast_rank_aliases_cache_lock = threading.Lock()
_forecast_rank_aliases_cache: Optional[Tuple[float, Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]]] = None

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _to_beijing_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert datetime to Asia/Shanghai for API response."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume DB stores UTC when tzinfo is missing.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_SHANGHAI_TZ)


def _format_beijing_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime to `YYYY-MM-DD HH:MM:SS` in Asia/Shanghai."""
    beijing = _to_beijing_datetime(dt)
    if beijing is None:
        return None
    return beijing.strftime("%Y-%m-%d %H:%M:%S")


def _load_forecast_type_rank_alias_tuples(db_session: Session) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    rows = db_session.exec(
        select(CRMSystemConfiguration.config_key, CRMSystemConfiguration.config_value).where(
            CRMSystemConfiguration.config_type == "ForecastTypeMapping",
            CRMSystemConfiguration.is_active.is_(True),
        )
    ).all()
    rank_to_aliases: Dict[int, List[str]] = {0: [], 1: [], 2: []}
    for config_key, config_value in rows:
        ck = str(config_key or "").strip().lower()
        rank = _FORECAST_TYPE_RANK_BY_CONFIG_KEY.get(ck)
        if rank is None:
            continue
        seen = set(rank_to_aliases[rank])
        for alias in _parse_forecast_type_aliases_from_config_value(config_value):
            if alias not in seen:
                seen.add(alias)
                rank_to_aliases[rank].append(alias)
    return (
        tuple(rank_to_aliases[0]),
        tuple(rank_to_aliases[1]),
        tuple(rank_to_aliases[2]),
    )


def _get_forecast_type_rank_alias_tuples_cached(
    db_session: Session,
) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    global _forecast_rank_aliases_cache
    now = time.monotonic()
    with _forecast_rank_aliases_cache_lock:
        if _forecast_rank_aliases_cache is not None:
            deadline, triple = _forecast_rank_aliases_cache
            if now < deadline:
                return triple
        triple = _load_forecast_type_rank_alias_tuples(db_session)
        _forecast_rank_aliases_cache = (now + _FORECAST_RANK_ALIASES_CACHE_TTL_SEC, triple)
        return triple


def _aldebaran_performance_payload_root(body: Dict[str, Any]) -> Dict[str, Any]:
    data = body.get("data")
    if isinstance(data, dict):
        return data
    return body


def _build_forecast_recalc_out_from_aldebaran(
    body: Dict[str, Any],
    *,
    recalc_scope: str,
    session_id: str,
) -> Dict[str, Any]:
    """将 Aldebaran 返回统一归一化为固定结构：total + attendees + pagination。"""
    node = _aldebaran_performance_payload_root(body)
    if not isinstance(node, dict):
        raise ValueError("Aldebaran recalculate response must be a JSON object")

    sid = str(node.get("session_id") or session_id or "").strip()
    if not sid:
        raise ValueError("Aldebaran recalculate response missing session_id")

    attendees_raw = node.get("attendees")
    if not isinstance(attendees_raw, list) and not str(node.get("owner_id", "") or "").strip():
        raise ValueError(
            "Aldebaran recalculate response must include `attendees` (full session) or `owner_id` (single owner)"
        )

    def _s(v: Any, default: str = "0") -> str:
        return default if v is None else str(v)

    def _f(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _i(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _metrics(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "target": _s(d.get("target")),
            "closed_amount": _s(d.get("closed_amount")),
            "commit_amount": _s(d.get("commit_amount")),
            "upside_amount": _s(d.get("upside_amount")),
            "gap": _s(d.get("gap")),
            "achievement_rate": _f(d.get("achievement_rate"), 0.0),
            "opportunity_count": _i(d.get("opportunity_count"), 0),
        }

    def _attendee(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "owner_id": str(d.get("owner_id", "") or "").strip(),
            "owner_name": str(d.get("owner_name", "") or "").strip(),
            "department_id": (str(d.get("department_id")).strip() if d.get("department_id") is not None else None),
            "department_name": (str(d.get("department_name")).strip() if d.get("department_name") is not None else None),
            "opportunities": d.get("opportunities"),
            **_metrics(d),
        }

    if isinstance(attendees_raw, list):
        attendees = [_attendee(a) for a in attendees_raw if isinstance(a, dict)]
        total = _metrics(node.get("total") if isinstance(node.get("total"), dict) else {})
        p = node.get("pagination") if isinstance(node.get("pagination"), dict) else {}
        pagination = {
            "page": _i(p.get("page"), 1),
            "page_size": _i(p.get("page_size"), 50),
            "total_pages": _i(p.get("total_pages"), 1),
            "total_items": _i(p.get("total_items"), len(attendees)),
        }
    else:
        one = _attendee(node)
        attendees = [one]
        total = _metrics(node)
        pagination = {"page": 1, "page_size": 50, "total_pages": 1, "total_items": 1}

    return {
        "session_id": sid,
        "fy_quarter": (str(node.get("fy_quarter")).strip() if node.get("fy_quarter") is not None else None),
        "recalc_scope": recalc_scope,
        "total": total,
        "attendees": attendees,
        "pagination": pagination,
    }


class CRMReviewService:
    @staticmethod
    def _model_to_dict(model_obj: Any) -> Dict[str, Any]:
        if hasattr(model_obj, "model_dump"):
            return model_obj.model_dump()
        if hasattr(model_obj, "dict"):
            return model_obj.dict()
        return dict(model_obj)

    def _query_risk_progress_by_opportunity_ids(
        self,
        db_session: Session,
        *,
        session_id: str,
        snapshot_period: str,
        opportunity_ids: List[str],
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        normalized_opp_ids = [
            str(opp_id).strip()
            for opp_id in (opportunity_ids or [])
            if str(opp_id or "").strip()
        ]
        if not normalized_opp_ids:
            return {}
        risk_rows = db_session.exec(
            select(CRMReviewOppRiskProgress)
            .where(
                CRMReviewOppRiskProgress.session_id == session_id,
                CRMReviewOppRiskProgress.snapshot_period == snapshot_period,
                CRMReviewOppRiskProgress.opportunity_id.in_(normalized_opp_ids),
                CRMReviewOppRiskProgress.record_type.in_(("RISK", "PROGRESS", "OPP_SUMMARY", "OPP_REQS_INSIGHT")),
            )
            .order_by(CRMReviewOppRiskProgress.detected_at.desc())
        ).all()

        by_opp: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for row in risk_rows:
            opp_id = str(getattr(row, "opportunity_id") or "").strip()
            if not opp_id:
                continue
            rtype = str(getattr(row, "record_type") or "").strip().upper()
            if rtype not in ("RISK", "PROGRESS", "OPP_SUMMARY", "OPP_REQS_INSIGHT"):
                continue
            bucket = by_opp.setdefault(
                opp_id,
                {"RISK": [], "PROGRESS": [], "OPP_SUMMARY": [], "OPP_REQS_INSIGHT": []},
            )
            bucket[rtype].append(self._model_to_dict(row))
        return by_opp

    def _attach_risk_progress_to_items(
        self,
        db_session: Session,
        *,
        session_id: str,
        snapshot_period: str,
        items: List[Any],
    ) -> List[Dict[str, Any]]:
        if not items:
            return []

        opportunity_ids = [
            str(getattr(item, "opportunity_id") or "").strip()
            for item in items
            if str(getattr(item, "opportunity_id") or "").strip()
        ]
        by_opp = self._query_risk_progress_by_opportunity_ids(
            db_session,
            session_id=session_id,
            snapshot_period=snapshot_period,
            opportunity_ids=opportunity_ids,
        )

        enriched: List[Dict[str, Any]] = []
        for item in items:
            row = self._model_to_dict(item)
            opp_id = str(getattr(item, "opportunity_id") or "").strip()
            opp_bucket = by_opp.get(
                opp_id,
                {"RISK": [], "PROGRESS": [], "OPP_SUMMARY": [], "OPP_REQS_INSIGHT": []},
            )
            row["risk_count"] = len(opp_bucket["RISK"])
            row["progress_count"] = len(opp_bucket["PROGRESS"])
            row["opp_summary_count"] = len(opp_bucket["OPP_SUMMARY"])
            row["opp_reqs_insight_count"] = len(opp_bucket["OPP_REQS_INSIGHT"])
            enriched.append(row)
        return enriched

    def _project_snapshot_items(
        self,
        *,
        items: List[Dict[str, Any]],
        fields_level: str,
    ) -> List[Dict[str, Any]]:
        level = str(fields_level or "basic").strip().lower()
        full_fields = tuple(CRMReviewOppBranchSnapshot.model_fields.keys())
        if level == "full":
            projected_full: List[Dict[str, Any]] = []
            for row in items:
                item = {k: row.get(k) for k in full_fields}
                item["risk_count"] = int(row.get("risk_count") or 0)
                item["progress_count"] = int(row.get("progress_count") or 0)
                item["opp_summary_count"] = int(row.get("opp_summary_count") or 0)
                item["opp_reqs_insight_count"] = int(row.get("opp_reqs_insight_count") or 0)
                projected_full.append(item)
            return projected_full

        basic_fields = tuple(CRMReviewOppBranchSnapshotBasicOut.model_fields.keys())
        projected: List[Dict[str, Any]] = []
        for row in items:
            item = {k: row.get(k) for k in basic_fields}
            item["risk_count"] = int(row.get("risk_count") or 0)
            item["progress_count"] = int(row.get("progress_count") or 0)
            item["opp_summary_count"] = int(row.get("opp_summary_count") or 0)
            item["opp_reqs_insight_count"] = int(row.get("opp_reqs_insight_count") or 0)
            projected.append(item)
        return projected

    @staticmethod
    def _build_forecast_type_rank_case(
        db_session: Session,
        snapshot_cls: Any = CRMReviewOppBranchSnapshot,
    ):
        """
        Sort key for forecast_type: commit (0) > upside (1) > closed_won (2).
        Aliases come from crm_system_configurations ForecastTypeMapping (config_value JSON arrays).
        snapshot.forecast_type matches one of those strings (e.g. 确定成单 -> commit).
        """
        a0, a1, a2 = _get_forecast_type_rank_alias_tuples_cached(db_session)
        ft_col = snapshot_cls.forecast_type
        whens: List[Any] = []
        if a0:
            whens.append((ft_col.in_(a0), 0))
        if a1:
            whens.append((ft_col.in_(a1), 1))
        if a2:
            whens.append((ft_col.in_(a2), 2))
        if not whens:
            ft_lower = func.lower(func.coalesce(ft_col, ""))
            return case(
                (ft_lower == "commit", 0),
                (ft_lower == "upside", 1),
                (ft_lower == "closed_won", 2),
                else_=99,
            )
        return case(*whens, else_=99)

    @staticmethod
    def _build_forecast_rank_case_for_col(db_session: Session, col: Any):
        a0, a1, a2 = _get_forecast_type_rank_alias_tuples_cached(db_session)
        whens: List[Any] = []
        if a0:
            whens.append((col.in_(a0), 0))
        if a1:
            whens.append((col.in_(a1), 1))
        if a2:
            whens.append((col.in_(a2), 2))
        if not whens:
            c_lower = func.lower(func.coalesce(col, ""))
            return case(
                (c_lower == "commit", 0),
                (c_lower == "upside", 1),
                (c_lower == "closed_won", 2),
                else_=99,
            )
        return case(*whens, else_=99)

    @staticmethod
    def _date_parse_expr(col: Any):
        return func.coalesce(
            func.str_to_date(col, "%Y-%m-%d %H:%i:%s"),
            func.str_to_date(col, "%Y-%m-%d"),
        )

    @staticmethod
    def _normalize_sorts_list(sorts: Optional[List[Tuple[str, str]]]) -> List[Tuple[str, str]]:
        if not sorts:
            return []
        out: List[Tuple[str, str]] = []
        for field, direction in sorts:
            f = str(field or "").strip()
            if not f:
                continue
            d = str(direction or "asc").strip().lower()
            if d not in ("asc", "desc"):
                d = "asc"
            out.append((f, d))
        return out

    def _snapshot_sort_expr_for_key(
        self,
        db_session: Session,
        key: str,
        *,
        session_id: str,
        snapshot_period: str,
        snapshot_cls: Any = CRMReviewOppBranchSnapshot,
    ) -> Optional[Any]:
        k = str(key or "").strip()
        if not k:
            return None
        if k == "forecast_type":
            return self._build_forecast_rank_case_for_col(db_session, snapshot_cls.forecast_type)
        if k == "ai_commit":
            return self._build_forecast_rank_case_for_col(db_session, snapshot_cls.ai_commit)
        if k == "opportunity_stage":
            return func.lower(func.coalesce(snapshot_cls.opportunity_stage, ""))
        if k == "ai_stage":
            return func.lower(func.coalesce(snapshot_cls.ai_stage, ""))
        if k == "forecast_amount":
            return func.coalesce(snapshot_cls.forecast_amount, 0)
        if k == "expected_closing_date":
            return self._date_parse_expr(snapshot_cls.expected_closing_date)
        if k == "ai_expected_closing_date":
            return self._date_parse_expr(snapshot_cls.ai_expected_closing_date)
        if k in {"risk_count", "progress_count", "opp_summary_count"}:
            record_type = (
                "RISK"
                if k == "risk_count"
                else ("PROGRESS" if k == "progress_count" else "OPP_SUMMARY")
            )
            return (
                select(func.count())
                .where(
                    CRMReviewOppRiskProgress.session_id == session_id,
                    CRMReviewOppRiskProgress.snapshot_period == snapshot_period,
                    CRMReviewOppRiskProgress.record_type == record_type,
                    CRMReviewOppRiskProgress.opportunity_id == snapshot_cls.opportunity_id,
                )
                .correlate(snapshot_cls)
                .scalar_subquery()
            )
        return None

    def _default_snapshot_sort_order(
        self,
        db_session: Session,
        snapshot_cls: Any = CRMReviewOppBranchSnapshot,
    ) -> List[Any]:
        ft_rank = self._build_forecast_type_rank_case(db_session, snapshot_cls=snapshot_cls)
        return [
            func.coalesce(snapshot_cls.owner_name, ""),
            ft_rank,
            snapshot_cls.forecast_amount.desc(),
        ]

    def _build_snapshot_sort_order(
        self,
        db_session: Session,
        *,
        sorts: List[Tuple[str, str]],
        session_id: str,
        snapshot_period: str,
        snapshot_cls: Any = CRMReviewOppBranchSnapshot,
    ) -> List[Any]:
        if not sorts:
            return self._default_snapshot_sort_order(db_session, snapshot_cls=snapshot_cls)

        out: List[Any] = []
        for field, direction in sorts:
            direction_desc = direction == "desc"
            expr = self._snapshot_sort_expr_for_key(
                db_session,
                field,
                session_id=session_id,
                snapshot_period=snapshot_period,
                snapshot_cls=snapshot_cls,
            )
            if expr is None:
                continue
            out.append(expr.desc() if direction_desc else expr.asc())

        if not out:
            return self._default_snapshot_sort_order(db_session, snapshot_cls=snapshot_cls)

        out.append(func.coalesce(snapshot_cls.owner_name, "").asc())
        out.append(snapshot_cls.id.desc())
        return out

    @staticmethod
    def _group_field_and_label(
        group_by: str,
        snapshot_cls: Any = CRMReviewOppBranchSnapshot,
    ):
        gb = str(group_by or "owner").strip()
        S = snapshot_cls
        if gb == "owner":
            return gb, S.owner_id, S.owner_name
        if gb == "forecast_type":
            return gb, S.forecast_type, S.forecast_type
        if gb == "opportunity_stage":
            return gb, S.opportunity_stage, S.opportunity_stage
        raise HTTPException(status_code=422, detail="group_by must be one of: owner, forecast_type, opportunity_stage")

    def _resolve_session_scope(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
    ) -> dict:
        session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="review session not found")

        attendee = crm_review_attendee_repo.get_by_session_and_user_id(
            db_session, session_id=session_id, user_id=user_id
        )
        if not attendee:
            raise HTTPException(status_code=403, detail="user is not attendee of this review session")

        is_leader = bool(getattr(attendee, "is_leader", False))
        if is_leader:
            owner_ids = crm_review_attendee_repo.get_crm_user_ids_by_session(
                db_session, session_id=session_id
            )
        else:
            owner_id = str(attendee.crm_user_id or "").strip()
            if not owner_id:
                raise HTTPException(status_code=422, detail="attendee has no crm_user_id")
            owner_ids = [owner_id]

        snapshot_period = str(session.period or "").strip()
        if not snapshot_period:
            raise HTTPException(status_code=500, detail="review session period is empty")

        submit_stats = crm_review_attendee_repo.get_submit_stats(db_session, session_id=session_id)
        editable = bool(
            session.stage == "initial_edit"
            or (session.stage == "lead_review" and session.review_phase == "edit")
        )

        return {
            "session": session,
            "is_leader": is_leader,
            "owner_ids": owner_ids,
            "snapshot_period": snapshot_period,
            "submit_stats": submit_stats,
            "editable": editable,
        }

    @staticmethod
    def _normalize_snapshot_filters(snapshot_filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Normalize extensible snapshot filters.
        Current supported fields:
        - opportunity_ids: List[str]
        - opportunity_names: List[str]
        - owner_ids: List[str]
        - owner_names: List[str]
        - forecast_types: List[str]
        - opportunity_stages: List[str]
        - expected_closing_date_start: str (YYYY-MM-DD)
        - expected_closing_date_end: str (YYYY-MM-DD)
        - forecast_amount_min: number
        - forecast_amount_max: number
        - ai_commits: List[str]
        - ai_stages: List[str]
        - ai_expected_closing_date_start: str (YYYY-MM-DD)
        - ai_expected_closing_date_end: str (YYYY-MM-DD)
        - has_risk: bool
        - has_progress: bool
        """
        raw = snapshot_filters if isinstance(snapshot_filters, dict) else {}

        def _normalize_string_list(values: Any) -> List[str]:
            out: List[str] = []
            if isinstance(values, list):
                seen: set[str] = set()
                for v in values:
                    s = str(v or "").strip()
                    if not s or s in seen:
                        continue
                    seen.add(s)
                    out.append(s)
            return out

        expected_closing_date_start = str(raw.get("expected_closing_date_start") or "").strip()
        expected_closing_date_end = str(raw.get("expected_closing_date_end") or "").strip()
        ai_expected_closing_date_start = str(raw.get("ai_expected_closing_date_start") or "").strip()
        ai_expected_closing_date_end = str(raw.get("ai_expected_closing_date_end") or "").strip()
        forecast_amount_min_raw = raw.get("forecast_amount_min")
        forecast_amount_max_raw = raw.get("forecast_amount_max")
        has_risk_raw = raw.get("has_risk")
        has_progress_raw = raw.get("has_progress")

        def _normalize_date_or_raise(value: str, field_name: str) -> Optional[str]:
            if not value:
                return None
            # Accept both date and datetime inputs.
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
            raise HTTPException(
                status_code=422,
                detail=f"invalid {field_name}, expected YYYY-MM-DD or YYYY-MM-DD HH:MM:SS",
            )

        def _normalize_float_or_raise(value: Any, field_name: str) -> Optional[float]:
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError) as e:
                raise HTTPException(
                    status_code=422,
                    detail=f"invalid {field_name}, expected number",
                ) from e

        def _normalize_bool_or_raise(value: Any, field_name: str) -> Optional[bool]:
            if value is None or value == "":
                return None
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"true", "1", "yes", "y"}:
                return True
            if text in {"false", "0", "no", "n"}:
                return False
            raise HTTPException(
                status_code=422,
                detail=f"invalid {field_name}, expected boolean",
            )

        return {
            "opportunity_ids": _normalize_string_list(raw.get("opportunity_ids")),
            "opportunity_names": _normalize_string_list(raw.get("opportunity_names")),
            "owner_ids": _normalize_string_list(raw.get("owner_ids")),
            "owner_names": _normalize_string_list(raw.get("owner_names")),
            "forecast_types": _normalize_string_list(raw.get("forecast_types")),
            "opportunity_stages": _normalize_string_list(raw.get("opportunity_stages")),
            "ai_commits": _normalize_string_list(raw.get("ai_commits")),
            "ai_stages": _normalize_string_list(raw.get("ai_stages")),
            "expected_closing_date_start": _normalize_date_or_raise(
                expected_closing_date_start, "expected_closing_date_start"
            ),
            "expected_closing_date_end": _normalize_date_or_raise(
                expected_closing_date_end, "expected_closing_date_end"
            ),
            "ai_expected_closing_date_start": _normalize_date_or_raise(
                ai_expected_closing_date_start, "ai_expected_closing_date_start"
            ),
            "ai_expected_closing_date_end": _normalize_date_or_raise(
                ai_expected_closing_date_end, "ai_expected_closing_date_end"
            ),
            "forecast_amount_min": _normalize_float_or_raise(
                forecast_amount_min_raw, "forecast_amount_min"
            ),
            "forecast_amount_max": _normalize_float_or_raise(
                forecast_amount_max_raw, "forecast_amount_max"
            ),
            "has_risk": _normalize_bool_or_raise(has_risk_raw, "has_risk"),
            "has_progress": _normalize_bool_or_raise(has_progress_raw, "has_progress"),
        }

    @staticmethod
    def _append_snapshot_filters(
        base_where: List[Any],
        normalized_filters: Dict[str, Any],
        *,
        session_id: str,
        snapshot_period: str,
        snapshot_cls: Any = CRMReviewOppBranchSnapshot,
    ) -> None:
        """
        Apply normalized filters onto snapshot query conditions.
        """
        S = snapshot_cls
        opportunity_ids = normalized_filters.get("opportunity_ids") or []
        if opportunity_ids:
            base_where.append(S.opportunity_id.in_(opportunity_ids))
        opportunity_names = normalized_filters.get("opportunity_names") or []
        if opportunity_names:
            base_where.append(S.opportunity_name.in_(opportunity_names))
        owner_ids = normalized_filters.get("owner_ids") or []
        if owner_ids:
            base_where.append(S.owner_id.in_(owner_ids))
        owner_names = normalized_filters.get("owner_names") or []
        if owner_names:
            base_where.append(S.owner_name.in_(owner_names))
        forecast_types = normalized_filters.get("forecast_types") or []
        if forecast_types:
            base_where.append(S.forecast_type.in_(forecast_types))
        opportunity_stages = normalized_filters.get("opportunity_stages") or []
        if opportunity_stages:
            base_where.append(S.opportunity_stage.in_(opportunity_stages))
        ai_commits = normalized_filters.get("ai_commits") or []
        if ai_commits:
            base_where.append(S.ai_commit.in_(ai_commits))
        ai_stages = normalized_filters.get("ai_stages") or []
        if ai_stages:
            base_where.append(S.ai_stage.in_(ai_stages))
        expected_closing_date_start = normalized_filters.get("expected_closing_date_start")
        if expected_closing_date_start:
            base_where.append(
                func.coalesce(
                    func.str_to_date(S.expected_closing_date, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(S.expected_closing_date, "%Y-%m-%d"),
                )
                >= func.coalesce(
                    func.str_to_date(expected_closing_date_start, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(expected_closing_date_start, "%Y-%m-%d"),
                )
            )
        expected_closing_date_end = normalized_filters.get("expected_closing_date_end")
        if expected_closing_date_end:
            base_where.append(
                func.coalesce(
                    func.str_to_date(S.expected_closing_date, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(S.expected_closing_date, "%Y-%m-%d"),
                )
                <= func.coalesce(
                    func.str_to_date(expected_closing_date_end, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(expected_closing_date_end, "%Y-%m-%d"),
                )
            )
        ai_expected_closing_date_start = normalized_filters.get("ai_expected_closing_date_start")
        if ai_expected_closing_date_start:
            base_where.append(
                func.coalesce(
                    func.str_to_date(S.ai_expected_closing_date, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(S.ai_expected_closing_date, "%Y-%m-%d"),
                )
                >= func.coalesce(
                    func.str_to_date(ai_expected_closing_date_start, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(ai_expected_closing_date_start, "%Y-%m-%d"),
                )
            )
        ai_expected_closing_date_end = normalized_filters.get("ai_expected_closing_date_end")
        if ai_expected_closing_date_end:
            base_where.append(
                func.coalesce(
                    func.str_to_date(S.ai_expected_closing_date, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(S.ai_expected_closing_date, "%Y-%m-%d"),
                )
                <= func.coalesce(
                    func.str_to_date(ai_expected_closing_date_end, "%Y-%m-%d %H:%i:%s"),
                    func.str_to_date(ai_expected_closing_date_end, "%Y-%m-%d"),
                )
            )
        forecast_amount_min = normalized_filters.get("forecast_amount_min")
        if forecast_amount_min is not None:
            base_where.append(S.forecast_amount >= forecast_amount_min)
        forecast_amount_max = normalized_filters.get("forecast_amount_max")
        if forecast_amount_max is not None:
            base_where.append(S.forecast_amount <= forecast_amount_max)
        has_risk = normalized_filters.get("has_risk")
        if has_risk is not None:
            risk_opp_subq = (
                select(CRMReviewOppRiskProgress.opportunity_id)
                .where(
                    CRMReviewOppRiskProgress.session_id == session_id,
                    CRMReviewOppRiskProgress.snapshot_period == snapshot_period,
                    CRMReviewOppRiskProgress.record_type == "RISK",
                )
                .distinct()
            )
            if has_risk:
                base_where.append(S.opportunity_id.in_(risk_opp_subq))
            else:
                base_where.append(S.opportunity_id.not_in(risk_opp_subq))
        has_progress = normalized_filters.get("has_progress")
        if has_progress is not None:
            progress_opp_subq = (
                select(CRMReviewOppRiskProgress.opportunity_id)
                .where(
                    CRMReviewOppRiskProgress.session_id == session_id,
                    CRMReviewOppRiskProgress.snapshot_period == snapshot_period,
                    CRMReviewOppRiskProgress.record_type == "PROGRESS",
                )
                .distinct()
            )
            if has_progress:
                base_where.append(S.opportunity_id.in_(progress_opp_subq))
            else:
                base_where.append(S.opportunity_id.not_in(progress_opp_subq))

    def get_my_edit_page_data(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
        page: int = 1,
        size: int = 20,
        fields_level: str = "basic",
        sorts: Optional[List[Tuple[str, str]]] = None,
        snapshot_filters: Optional[Dict[str, Any]] = None,
    ) -> dict:
        page = int(page or 1)
        size = int(size or 20)
        if page < 1:
            page = 1
        if size < 1:
            size = 20
        offset = (page - 1) * size
        scope = self._resolve_session_scope(db_session, session_id=session_id, user_id=user_id)
        owner_ids = [str(x).strip() for x in (scope["owner_ids"] or []) if str(x or "").strip()]
        if not owner_ids:
            raise HTTPException(status_code=422, detail="no attendee crm_user_id in this review session")
        snapshot_period = scope["snapshot_period"]
        normalized_filters = self._normalize_snapshot_filters(snapshot_filters)
        snap = CRMReviewOppBranchSnapshotCache
        base_where: List[Any] = [
            snap.owner_id.in_(owner_ids),
            snap.snapshot_period == snapshot_period,
        ]
        self._append_snapshot_filters(
            base_where,
            normalized_filters,
            session_id=session_id,
            snapshot_period=snapshot_period,
            snapshot_cls=snap,
        )

        total = int(
            db_session.exec(
                select(func.count()).select_from(snap).where(*base_where)
            ).one()
        )
        norm_sorts = self._normalize_sorts_list(sorts)
        order_by = self._build_snapshot_sort_order(
            db_session,
            sorts=norm_sorts,
            session_id=session_id,
            snapshot_period=snapshot_period,
            snapshot_cls=snap,
        )
        items = db_session.exec(
            select(snap)
            .where(*base_where)
            .order_by(*order_by)
            .offset(offset)
            .limit(size)
        ).all()
        enriched_items = self._attach_risk_progress_to_items(
            db_session,
            session_id=session_id,
            snapshot_period=snapshot_period,
            items=items,
        )
        output_items = self._project_snapshot_items(
            items=enriched_items,
            fields_level=fields_level,
        )

        return {
            "session_id": str(scope["session"].unique_id),
            "page": page,
            "size": size,
            "total": total,
            "items": output_items,
        }

    def list_snapshot_groups(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
        group_by: str = "owner",
        sorts: Optional[List[Tuple[str, str]]] = None,
        snapshot_filters: Optional[Dict[str, Any]] = None,
    ) -> dict:
        scope = self._resolve_session_scope(db_session, session_id=session_id, user_id=user_id)
        normalized_filters = self._normalize_snapshot_filters(snapshot_filters)
        norm = self._normalize_sorts_list(sorts)
        first_sort = norm[0] if norm else None
        sort_by = first_sort[0] if first_sort else None
        direction_desc = first_sort[1] == "desc" if first_sort else False

        snap = CRMReviewOppBranchSnapshotCache
        base_where: List[Any] = [
            snap.owner_id.in_(scope["owner_ids"]),
            snap.snapshot_period == scope["snapshot_period"],
        ]
        self._append_snapshot_filters(
            base_where,
            normalized_filters,
            session_id=session_id,
            snapshot_period=scope["snapshot_period"],
            snapshot_cls=snap,
        )

        def _group_order_by(cnt_expr: Any, key_expr: Any) -> List[Any]:
            # 分组结果排序（仅使用 sorts 的第一项，与原先单字段语义一致）：
            # - 未指定：仅按分组 key 升序
            # - risk_count / progress_count / opp_summary_count：按数量排序
            # - 其他：按分组 key 与 direction
            sk = str(sort_by or "").strip()
            if not sk:
                return [key_expr.asc()]
            if sk in {"risk_count", "progress_count", "opp_summary_count"}:
                primary = cnt_expr.desc() if direction_desc else cnt_expr.asc()
                return [primary, key_expr.asc()]
            primary = key_expr.desc() if direction_desc else key_expr.asc()
            return [primary, key_expr.asc()]

        gb, field_col, label_col = self._group_field_and_label(group_by, snapshot_cls=snap)
        group_key_expr = func.coalesce(field_col, "")
        group_label_expr = func.coalesce(func.max(label_col), "")
        cnt_expr = func.count()
        stmt = (
            select(
                group_key_expr.label("group_key"),
                group_label_expr.label("group_label"),
                cnt_expr.label("cnt"),
            )
            .where(*base_where)
            .group_by(group_key_expr)
            .order_by(*_group_order_by(cnt_expr, group_key_expr))
        )
        rows = db_session.exec(stmt).all()
        groups = [
            {
                "group_key": str(k or ""),
                "group_label": str(lbl or ""),
                "count": int(cnt or 0),
            }
            for k, lbl, cnt in rows
        ]
        return {
            "session_id": str(scope["session"].unique_id),
            "session": {
                "session_id": scope["session"].unique_id,
                "period": scope["session"].period,
                "period_start": scope["session"].period_start,
                "period_end": scope["session"].period_end,
                "stage": scope["session"].stage,
                "report_date": scope["session"].report_date,
                "create_time": _format_beijing_datetime(scope["session"].create_time),
                "review_phase": scope["session"].review_phase,
            },
            "can_review": bool(scope["is_leader"]) and str(scope["session"].stage or "").strip() == "lead_review",
            "is_leader": scope["is_leader"],
            "editable": scope["editable"],
            "submit_stats": scope["submit_stats"],
            "group_by": gb,
            "total_groups": len(groups),
            "groups": groups,
        }

    def query_snapshot_group_data(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
        group_by: str,
        group_key: str,
        page: int = 1,
        size: int = 20,
        fields_level: str = "basic",
        sorts: Optional[List[Tuple[str, str]]] = None,
        snapshot_filters: Optional[Dict[str, Any]] = None,
    ) -> dict:
        page = int(page or 1)
        size = int(size or 20)
        if page < 1:
            page = 1
        if size < 1:
            size = 20
        offset = (page - 1) * size

        scope = self._resolve_session_scope(db_session, session_id=session_id, user_id=user_id)
        group_key = str(group_key or "")
        normalized_filters = self._normalize_snapshot_filters(snapshot_filters)

        snap = CRMReviewOppBranchSnapshotCache
        base_where = [
            snap.owner_id.in_(scope["owner_ids"]),
            snap.snapshot_period == scope["snapshot_period"],
        ]
        gb, field_col, _ = self._group_field_and_label(group_by, snapshot_cls=snap)
        if group_key == "__EMPTY__":
            base_where.append(func.coalesce(field_col, "") == "")
        else:
            base_where.append(func.coalesce(field_col, "") == group_key)
        self._append_snapshot_filters(
            base_where,
            normalized_filters,
            session_id=session_id,
            snapshot_period=scope["snapshot_period"],
            snapshot_cls=snap,
        )

        total = int(
            db_session.exec(
                select(func.count()).select_from(snap).where(*base_where)
            ).one()
        )
        norm_sorts = self._normalize_sorts_list(sorts)
        order_by = self._build_snapshot_sort_order(
            db_session,
            sorts=norm_sorts,
            session_id=session_id,
            snapshot_period=scope["snapshot_period"],
            snapshot_cls=snap,
        )
        items = db_session.exec(
            select(snap)
            .where(*base_where)
            .order_by(*order_by)
            .offset(offset)
            .limit(size)
        ).all()
        enriched_items = self._attach_risk_progress_to_items(
            db_session,
            session_id=session_id,
            snapshot_period=scope["snapshot_period"],
            items=items,
        )
        output_items = self._project_snapshot_items(
            items=enriched_items,
            fields_level=fields_level,
        )

        return {
            "session_id": str(scope["session"].unique_id),
            "group_by": gb,
            "group_key": group_key,
            "page": page,
            "size": size,
            "total": total,
            "items": output_items,
        }

    def get_opportunity_risk_progress_details(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
        opportunity_id: str,
    ) -> dict:
        scope = self._resolve_session_scope(db_session, session_id=session_id, user_id=user_id)
        snapshot_period = scope["snapshot_period"]
        opportunity_id = str(opportunity_id or "").strip()
        if not opportunity_id:
            raise HTTPException(status_code=422, detail="opportunity_id is required")

        visible = db_session.exec(
            select(CRMReviewOppBranchSnapshot.unique_id)
            .where(
                CRMReviewOppBranchSnapshot.owner_id.in_(scope["owner_ids"]),
                CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
                CRMReviewOppBranchSnapshot.opportunity_id == opportunity_id,
            )
            .limit(1)
        ).first()
        if not visible:
            raise HTTPException(status_code=404, detail="opportunity not found in current review scope")

        return self._build_opportunity_risk_progress_details(
            db_session,
            session_id=session_id,
            snapshot_period=snapshot_period,
            opportunity_id=opportunity_id,
        )

    def _build_opportunity_risk_progress_details(
        self,
        db_session: Session,
        *,
        session_id: str,
        snapshot_period: str,
        opportunity_id: str,
    ) -> dict:
        snapshot = db_session.exec(
            select(CRMReviewOppBranchSnapshot)
            .where(
                CRMReviewOppBranchSnapshot.opportunity_id == opportunity_id,
                CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
            )
            .order_by(
                CRMReviewOppBranchSnapshot.update_time.desc(),
                CRMReviewOppBranchSnapshot.create_time.desc(),
            )
            .limit(1)
        ).first()

        by_opp = self._query_risk_progress_by_opportunity_ids(
            db_session,
            session_id=session_id,
            snapshot_period=snapshot_period,
            opportunity_ids=[opportunity_id],
        )
        opp_bucket = by_opp.get(
            opportunity_id,
            {"RISK": [], "PROGRESS": [], "OPP_SUMMARY": [], "OPP_REQS_INSIGHT": []},
        )
        return {
            "session_id": str(session_id),
            "opportunity_id": opportunity_id,
            "snapshot_period": snapshot_period,
            "snapshot_basic": {
                "opportunity_id": (getattr(snapshot, "opportunity_id", None) or opportunity_id),
                "account_id": getattr(snapshot, "account_id", None),
                "opportunity_name": getattr(snapshot, "opportunity_name", None),
                "account_name": getattr(snapshot, "account_name", None),
                "owner_id": getattr(snapshot, "owner_id", None),
                "owner_name": getattr(snapshot, "owner_name", None),
                "forecast_type": getattr(snapshot, "forecast_type", None),
                "forecast_amount": getattr(snapshot, "forecast_amount", None),
                "opportunity_stage": getattr(snapshot, "opportunity_stage", None),
                "expected_closing_date": getattr(snapshot, "expected_closing_date", None),
                "stage_stay": getattr(snapshot, "stage_stay", None),
                "ai_commit": getattr(snapshot, "ai_commit", None),
                "ai_stage": getattr(snapshot, "ai_stage", None),
                "ai_expected_closing_date": getattr(snapshot, "ai_expected_closing_date", None),
            },
            "risk_count": len(opp_bucket["RISK"]),
            "progress_count": len(opp_bucket["PROGRESS"]),
            "opp_summary_count": len(opp_bucket["OPP_SUMMARY"]),
            "opp_reqs_insight_count": len(opp_bucket["OPP_REQS_INSIGHT"]),
            "risk_details": opp_bucket["RISK"],
            "progress_details": opp_bucket["PROGRESS"],
            "opp_summary_details": opp_bucket["OPP_SUMMARY"],
            "opp_reqs_insight_details": opp_bucket["OPP_REQS_INSIGHT"],
        }

    def get_opportunity_risk_progress_details_by_latest_session(
        self,
        db_session: Session,
        *,
        opportunity_id: str,
        session_id: Optional[str] = None,
    ) -> dict:
        opportunity_id = str(opportunity_id or "").strip()
        if not opportunity_id:
            raise HTTPException(status_code=422, detail="opportunity_id is required")

        sid = str(session_id or "").strip()
        # Snapshot lists all opps for a period; risk rows exist only for some. Resolve session via period join.
        if sid:
            resolved = crm_review_session_repo.get_by_unique_id(db_session, sid)
        else:
            resolved = db_session.exec(
                select(CRMReviewSession)
                .join(
                    CRMReviewOppBranchSnapshot,
                    (CRMReviewOppBranchSnapshot.snapshot_period == CRMReviewSession.period)
                    & (CRMReviewOppBranchSnapshot.opportunity_id == opportunity_id),
                )
                .order_by(CRMReviewSession.report_date.desc(), CRMReviewSession.create_time.desc())
                .limit(1)
            ).first()
        if not resolved:
            # TODO：暂时查询商机表，后续改为查询表4最新报告
            opp = db_session.exec(
                select(CRMOpportunity)
                .where(CRMOpportunity.unique_id == opportunity_id)
                .limit(1)
            ).first()
            if not opp:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "指定review session中未找到目标商机，且商机表中亦未找到目标商机，请检查商机ID是否正确"
                        if sid
                        else "未找到目标商机，请检查商机ID是否正确"
                    ),
                )

            return {
                "session_id": sid,
                "opportunity_id": opportunity_id,
                "snapshot_period": "",
                "snapshot_basic": {
                    "opportunity_id": (getattr(opp, "unique_id", None) or opportunity_id),
                    "account_id": getattr(opp, "customer_id", None),
                    "opportunity_name": getattr(opp, "opportunity_name", None),
                    "account_name": getattr(opp, "customer_name", None),
                    "owner_id": getattr(opp, "owner_id", None),
                    "owner_name": getattr(opp, "owner", None),
                    "forecast_type": getattr(opp, "forecast_type", None),
                    "forecast_amount": None,
                    "opportunity_stage": getattr(opp, "opportunity_stage", None),
                    "expected_closing_date": getattr(opp, "expected_closing_date", None),
                    "stage_stay": None,
                    "ai_commit": None,
                    "ai_stage": None,
                    "ai_expected_closing_date": None,
                },
                "risk_count": 0,
                "progress_count": 0,
                "opp_summary_count": 0,
                "opp_reqs_insight_count": 0,
                "risk_details": [],
                "progress_details": [],
                "opp_summary_details": [],
                "opp_reqs_insight_details": [],
            }

        snapshot_period = str(resolved.period or "").strip()
        if not snapshot_period:
            raise HTTPException(status_code=500, detail="review session period is empty")

        if sid and not db_session.exec(
            select(CRMReviewOppBranchSnapshot.unique_id)
            .where(
                CRMReviewOppBranchSnapshot.opportunity_id == opportunity_id,
                CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
            )
            .limit(1)
        ).first():
            raise HTTPException(
                status_code=404,
                detail="opportunity not found in specified review session",
            )

        return self._build_opportunity_risk_progress_details(
            db_session,
            session_id=str(resolved.unique_id),
            snapshot_period=snapshot_period,
            opportunity_id=opportunity_id,
        )

    def submit_my_snapshot_changes(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
        updates: List[Dict[str, Any]],
    ) -> dict:
        session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="review session not found")

        attendee = crm_review_attendee_repo.get_by_session_and_user_id(
            db_session, session_id=session_id, user_id=user_id
        )
        if not attendee:
            raise HTTPException(status_code=403, detail="user is not attendee of this review session")

        can_edit = bool(
            session.stage == "initial_edit"
            or (session.stage == "lead_review" and session.review_phase == "edit")
        )
        if not can_edit:
            raise HTTPException(status_code=409, detail="review session is not editable")

        snapshot_period = session.period
        owner_crm_user_id = attendee.crm_user_id
        is_leader = bool(getattr(attendee, "is_leader", False))

        # If caller submits "no changes" (empty updates array),
        # we still need to record the submit attempt for stats/audit.
        updates = updates or []
        if len(updates) == 0:
            audit = CRMReviewOppAuditLog(
                session_id=str(session.unique_id),
                change_scope="submit_empty_updates",
                updated_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                old_value=json.dumps(
                    {
                        "period": snapshot_period,
                        "attempted_updates": {},
                        "changed_snapshot_unique_ids": [],
                        "unchanged_snapshot_unique_ids": [],
                    },
                    ensure_ascii=False,
                ),
                new_value=json.dumps(
                    {
                        "period": snapshot_period,
                        "attempted_updates": {},
                        "changed_snapshot_unique_ids": [],
                        "unchanged_snapshot_unique_ids": [],
                    },
                    ensure_ascii=False,
                ),
                change_type="UPDATE",
                edit_phase=str(session.stage),
                updated_by=str(attendee.user_name or "unknown"),
                updated_by_id=str(user_id),
            )
            try:
                crm_review_opp_audit_log_repo.create_audit(db_session, audit)
            except Exception as e:  # noqa: BLE001
                # Audit writing must never break the submit main flow.
                # (e.g. audit table schema/constraints temporarily mismatched)
                logger.warning(
                    "crm_review_opp_audit_log write failed (ignored): session_id=%s user_id=%s err=%s",
                    session_id,
                    user_id,
                    e,
                    exc_info=True,
                )
                db_session.rollback()

            submit_stats = crm_review_attendee_repo.get_submit_stats(
                db_session, session_id=session_id
            )
            return {"updated_count": 0, "submit_stats": submit_stats}

        # Normalize updates and enforce field whitelist
        normalized: List[Dict[str, Any]] = []
        snapshot_unique_ids: List[str] = []
        submitted_snapshot_unique_ids: set[str] = set()
        for u in updates or []:
            if not isinstance(u, dict):
                continue
            snapshot_unique_id = str(u.get("unique_id") or "").strip()
            if not snapshot_unique_id:
                continue
            client_version = u.get("version")
            if not isinstance(client_version, int):
                raise HTTPException(status_code=422, detail="invalid version")
            patch: Dict[str, Any] = {"unique_id": snapshot_unique_id}
            has_any = False
            for f in EDITABLE_FIELDS:
                if f in u:
                    patch[f] = u.get(f)
                    has_any = True
            if not has_any:
                continue
            patch["version"] = int(client_version)
            normalized.append(patch)
            snapshot_unique_ids.append(snapshot_unique_id)
            submitted_snapshot_unique_ids.add(snapshot_unique_id)

        if not normalized:
            raise HTTPException(status_code=422, detail="no valid updates")

        snap_repo = crm_review_opp_branch_snapshot_cache_repo
        if is_leader:
            owner_crm_user_ids = crm_review_attendee_repo.get_crm_user_ids_by_session(
                db_session, session_id=session_id
            )
            rows = snap_repo.get_by_owner_ids_period_and_snapshot_unique_ids(
                db_session,
                owner_crm_user_ids=owner_crm_user_ids,
                snapshot_period=snapshot_period,
                snapshot_unique_ids=snapshot_unique_ids,
            )
        else:
            rows = snap_repo.get_by_owner_period_and_snapshot_unique_ids(
                db_session,
                owner_crm_user_id=owner_crm_user_id,
                snapshot_period=snapshot_period,
                snapshot_unique_ids=snapshot_unique_ids,
            )
        rows_by_snapshot_unique_id = {r.unique_id: r for r in rows}
        if not rows_by_snapshot_unique_id:
            raise HTTPException(status_code=404, detail="no branch snapshots found for updates")

        # 如果提交里包含了无权限的 snapshot unique_id：直接报错，避免静默忽略
        unknown_snapshot_unique_ids = [
            sid for sid in submitted_snapshot_unique_ids if sid not in rows_by_snapshot_unique_id
        ]
        if unknown_snapshot_unique_ids:
            raise HTTPException(
                status_code=403 if not is_leader else 422,
                detail=f"some snapshot unique_id are not editable: {unknown_snapshot_unique_ids[:10]}",
            )

        before: Dict[str, Any] = {}
        after: Dict[str, Any] = {}
        updated_count = 0
        changed_snapshot_unique_ids: set[str] = set()
        unchanged_snapshot_unique_ids: set[str] = set()
        conflict_snapshot_unique_ids: List[str] = []

        for patch in normalized:
            snapshot_unique_id = patch["unique_id"]
            row = rows_by_snapshot_unique_id.get(snapshot_unique_id)
            if not row:
                continue

            client_version = int(patch.get("version") or 0)
            db_version = int(getattr(row, "modification_count", 0) or 0)
            if db_version != client_version:
                conflict_snapshot_unique_ids.append(snapshot_unique_id)
                continue

            before_fields = {f: getattr(row, f) for f in EDITABLE_FIELDS}
            for f in EDITABLE_FIELDS:
                if f in patch:
                    setattr(row, f, patch.get(f))
            after_fields = {f: getattr(row, f) for f in EDITABLE_FIELDS}

            # Always record submit operation for audit/statistics,
            # even when the submitted values equal existing values.
            before[snapshot_unique_id] = before_fields
            after[snapshot_unique_id] = after_fields
            if before_fields != after_fields:
                updated_count += 1
                changed_snapshot_unique_ids.add(snapshot_unique_id)
                now = datetime.now(timezone.utc)
                row.update_time = now
                row.last_modified_by_id = str(user_id)
                row.last_modified_by = str(attendee.user_name or "unknown")
                row.was_modified = True
                row.modification_count = db_version + 1
                if str(session.stage) == "initial_edit":
                    row.initial_edit_modification_count = int(
                        getattr(row, "initial_edit_modification_count", 0) or 0
                    ) + 1
                elif str(session.stage) == "lead_review":
                    row.meeting_edit_modification_count = int(
                        getattr(row, "meeting_edit_modification_count", 0) or 0
                    ) + 1
            else:
                unchanged_snapshot_unique_ids.add(snapshot_unique_id)

        if conflict_snapshot_unique_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "snapshot was modified by someone else, please refresh and retry",
                    "conflict_snapshot_unique_ids": conflict_snapshot_unique_ids[:20],
                },
            )

        # Persist updates only when we actually changed any field.
        if updated_count > 0:
            db_session.add_all(list(rows_by_snapshot_unique_id.values()))

        db_session.commit()

        # One audit log per submit
        # Distinguish different submit intents for easier debugging.
        # - non-empty updates payload:
        #   - some opps actually changed => partial/all changed
        #   - no opp fields changed       => no_field_changes
        if changed_snapshot_unique_ids and unchanged_snapshot_unique_ids:
            change_scope = "batch_submit_partial_changes"
        elif changed_snapshot_unique_ids:
            change_scope = "batch_submit_all_changed"
        else:
            change_scope = "batch_submit_no_field_changes"
        audit = CRMReviewOppAuditLog(
            session_id=str(session.unique_id),
            change_scope=change_scope,
            updated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            old_value=json.dumps(
                {
                    "period": snapshot_period,
                    "attempted_updates": before,
                    "changed_snapshot_unique_ids": sorted(changed_snapshot_unique_ids),
                    "unchanged_snapshot_unique_ids": sorted(unchanged_snapshot_unique_ids),
                },
                ensure_ascii=False,
                default=_audit_json_default,
            ),
            new_value=json.dumps(
                {
                    "period": snapshot_period,
                    "attempted_updates": after,
                    "changed_snapshot_unique_ids": sorted(changed_snapshot_unique_ids),
                    "unchanged_snapshot_unique_ids": sorted(unchanged_snapshot_unique_ids),
                },
                ensure_ascii=False,
                default=_audit_json_default,
            ),
            change_type="UPDATE",
            edit_phase=str(session.stage),
            updated_by=str(attendee.user_name or "unknown"),
            updated_by_id=str(user_id),
        )
        try:
            crm_review_opp_audit_log_repo.create_audit(db_session, audit)
        except Exception as e:  # noqa: BLE001
            # Audit writing must never break the submit main flow.
            logger.warning(
                "crm_review_opp_audit_log write failed (ignored): session_id=%s user_id=%s err=%s",
                session_id,
                user_id,
                e,
                exc_info=True,
            )
            db_session.rollback()

        submit_stats = crm_review_attendee_repo.get_submit_stats(db_session, session_id=session_id)
        return {"updated_count": updated_count, "submit_stats": submit_stats}

    def merge_branch_snapshots_from_cache_to_main(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
    ) -> dict:
        """
        Session leader only: cache mirrors main; sales change submit-whitelisted fields
        and submit metadata on cache. This merge copies those columns from cache → main
        by (opportunity_id, snapshot_period). Each cache row should have a matching main
        row; rows without a match are skipped and logged at error level (no HTTP error).

        Cache rows are read in batches (keyset on cache PK ``id``) to avoid loading the full
        result set at once when data volume is large.
        """
        session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="review session not found")

        attendee = crm_review_attendee_repo.get_by_session_and_user_id(
            db_session, session_id=session_id, user_id=user_id
        )
        if not attendee:
            raise HTTPException(status_code=403, detail="user is not attendee of this review session")
        if not bool(getattr(attendee, "is_leader", False)):
            raise HTTPException(status_code=403, detail="only session leader can merge cache into main snapshots")

        snapshot_period = str(session.period or "").strip()
        if not snapshot_period:
            raise HTTPException(status_code=422, detail="review session period is empty")

        owner_ids = [
            str(x).strip()
            for x in crm_review_attendee_repo.get_crm_user_ids_by_session(
                db_session, session_id=session_id
            )
            if str(x or "").strip()
        ]
        if not owner_ids:
            raise HTTPException(status_code=422, detail="no attendee crm_user_id in this review session")

        Cache = CRMReviewOppBranchSnapshotCache
        Main = CRMReviewOppBranchSnapshot

        missing_keys: list[tuple[str, str]] = []
        main_updated = 0
        audit_ops: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        cache_rows_scanned = 0
        last_cache_id = 0

        while True:
            batch_where: List[Any] = [
                Cache.snapshot_period == snapshot_period,
                Cache.owner_id.in_(owner_ids),
            ]
            if last_cache_id:
                batch_where.append(Cache.id > last_cache_id)
            batch = list(
                db_session.exec(
                    select(Cache)
                    .options(load_only(*_MERGE_CACHE_LOAD_COLUMNS))
                    .where(*batch_where)
                    .order_by(Cache.id.asc())
                    .limit(MERGE_CACHE_BATCH_SIZE)
                ).all()
            )
            if not batch:
                break
            cache_rows_scanned += len(batch)
            batch_ids_int = [int(i) for i in (getattr(r, "id", None) for r in batch) if i is not None]
            if not batch_ids_int:
                logger.error(
                    "merge cache→main: cache batch rows missing integer id, cannot keyset-paginate. "
                    "session_id=%s snapshot_period=%s batch_size=%s",
                    session_id,
                    snapshot_period,
                    len(batch),
                )
                break
            last_cache_id = max(batch_ids_int)

            opp_ids = sorted(
                {
                    str(getattr(c, "opportunity_id", "") or "").strip()
                    for c in batch
                    if str(getattr(c, "opportunity_id", "") or "").strip()
                }
            )
            main_by_opp_period: dict[tuple[str, str], Any] = {}
            if opp_ids:
                main_rows = list(
                    db_session.exec(
                        select(Main)
                        .options(load_only(*_MERGE_MAIN_LOAD_COLUMNS))
                        .where(
                            Main.snapshot_period == snapshot_period,
                            Main.opportunity_id.in_(opp_ids),
                        )
                    ).all()
                )
                for m in main_rows:
                    oid_m = str(getattr(m, "opportunity_id", "") or "").strip()
                    per_m = str(getattr(m, "snapshot_period", "") or "").strip()
                    if oid_m and per_m:
                        main_by_opp_period[(oid_m, per_m)] = m

            for c in batch:
                oid = str(getattr(c, "opportunity_id", "") or "").strip()
                per = str(getattr(c, "snapshot_period", "") or "").strip()
                if not oid or not per:
                    continue
                key = (oid, per)
                target = main_by_opp_period.get(key)
                if target is None:
                    missing_keys.append(key)
                    continue
                before_editable = {f: getattr(target, f) for f in REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS}
                cache_editable = {f: getattr(c, f) for f in REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS}
                before_submit_sync = {
                    f: getattr(target, f) for f in _MERGE_SUBMIT_SYNC_FIELD_NAMES
                }
                cache_submit_sync = {f: getattr(c, f) for f in _MERGE_SUBMIT_SYNC_FIELD_NAMES}
                if before_editable == cache_editable and before_submit_sync == cache_submit_sync:
                    continue
                for f in REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS:
                    setattr(target, f, getattr(c, f))
                for f in _MERGE_SUBMIT_SYNC_FIELD_NAMES:
                    setattr(target, f, getattr(c, f))
                target.was_changed_to_commit = True
                db_session.add(target)
                main_updated += 1
                audit_ops.append(
                    {
                        "op": "update",
                        "opportunity_id": oid,
                        "main_unique_id": str(getattr(target, "unique_id", "") or ""),
                        "cache_unique_id": str(getattr(c, "unique_id", "") or ""),
                        "before_editable": before_editable,
                        "after_editable": cache_editable,
                        "before_submit_sync": before_submit_sync,
                        "after_submit_sync": cache_submit_sync,
                    }
                )

        if missing_keys:
            uniq_opp = sorted({k[0] for k in missing_keys})
            logger.error(
                "merge cache→main: %s cache row(s) have no matching main snapshot (skipped). "
                "session_id=%s snapshot_period=%s distinct_opportunity_id_count=%s "
                "sample_opportunity_ids=%s sample_keys=%s",
                len(missing_keys),
                session_id,
                snapshot_period,
                len(uniq_opp),
                uniq_opp[:30],
                missing_keys[:15],
            )

        db_session.commit()

        audit = CRMReviewOppAuditLog(
            session_id=str(session.unique_id),
            change_scope="leader_merge_cache_to_main",
            updated_at=now,
            created_at=now,
            old_value=json.dumps(
                {
                    "snapshot_period": snapshot_period,
                    "event": "leader_merge_cache_to_main",
                },
                ensure_ascii=False,
            ),
            new_value=json.dumps(
                {
                    "snapshot_period": snapshot_period,
                    "summary": {
                        "cache_rows_scanned": cache_rows_scanned,
                        "main_rows_updated": main_updated,
                        "skipped_cache_rows_no_main": len(missing_keys),
                    },
                    "ops": audit_ops,
                },
                ensure_ascii=False,
                default=_audit_json_default,
            ),
            change_type="UPDATE",
            edit_phase=str(session.stage),
            updated_by=str(attendee.user_name or "unknown"),
            updated_by_id=str(user_id),
        )
        try:
            crm_review_opp_audit_log_repo.create_audit(db_session, audit)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "crm_review_opp_audit_log write failed after merge (ignored): session_id=%s user_id=%s err=%s",
                session_id,
                user_id,
                e,
                exc_info=True,
            )
            db_session.rollback()

        return {
            "session_id": str(session.unique_id),
            "snapshot_period": snapshot_period,
            "cache_rows_scanned": cache_rows_scanned,
            "main_rows_updated": main_updated,
            "skipped_cache_rows_no_main": len(missing_keys),
        }

    def audit_submit_button_click(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
    ) -> dict:
        """Record one audit row per submit-button click."""
        session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="review session not found")
        if str(session.stage) != "initial_edit":
            raise HTTPException(
                status_code=409,
                detail="submit click audit can only be recorded when session.stage is initial_edit",
            )

        attendee = crm_review_attendee_repo.get_by_session_and_user_id(
            db_session, session_id=session_id, user_id=user_id
        )
        if not attendee:
            raise HTTPException(status_code=403, detail="user is not attendee of this review session")

        now = datetime.now(timezone.utc)
        # Keep submit_stats behavior unchanged: each click marks attendee as submitted
        # and bumps submission_count on CRMReviewAttendee.
        attendee.has_submitted = True
        attendee.submitted_at = now
        attendee.submission_count = (attendee.submission_count or 0) + 1
        db_session.add(attendee)
        db_session.commit()

        audit = CRMReviewOppAuditLog(
            session_id=str(session.unique_id),
            change_scope="submit_button_click",
            updated_at=now,
            created_at=now,
            old_value=json.dumps(
                {
                    "event": "submit_button_click",
                    "period": str(session.period or ""),
                },
                ensure_ascii=False,
            ),
            new_value=json.dumps(
                {
                    "event": "submit_button_click",
                    "period": str(session.period or ""),
                    "clicked_at": now.isoformat(),
                },
                ensure_ascii=False,
            ),
            change_type="UPDATE",
            edit_phase=str(session.stage),
            updated_by=str(attendee.user_name or "unknown"),
            updated_by_id=str(user_id),
        )
        try:
            crm_review_opp_audit_log_repo.create_audit(db_session, audit)
        except Exception as e:  # noqa: BLE001
            db_session.rollback()
            logger.warning(
                "submit_button_click audit write failed (ignored): session_id=%s user_id=%s err=%s",
                session_id,
                user_id,
                e,
                exc_info=True,
            )

        return {"session_id": str(session.unique_id), "recorded": True}

    def recalculate_forecast_aggregates(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
    ) -> dict:
        """
        forecast 聚合仅以 Aldebaran ``POST .../review/performance/query`` 返回为准。
        - Leader：请求仅 ``session_id``（全量）。
        - 普通参会人：``session_id`` + ``owner_id``（crm_user_id）。
        """
        session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="review session not found")

        attendee = crm_review_attendee_repo.get_by_session_and_user_id(
            db_session, session_id=session_id, user_id=user_id
        )
        if not attendee:
            raise HTTPException(status_code=403, detail="user is not attendee of this review session")

        is_leader = bool(getattr(attendee, "is_leader", False))
        owner_id_arg: Optional[str] = None

        if is_leader:
            attendees = db_session.exec(
                select(CRMReviewAttendee).where(CRMReviewAttendee.session_id == session_id)
            ).all()
            owner_ids = [str(a.crm_user_id).strip() for a in attendees if a and (a.crm_user_id or "").strip()]
            if not owner_ids:
                raise HTTPException(status_code=422, detail="no attendees with crm_user_id for this session")
            recalc_scope = "full_session"
        else:
            oid = str(attendee.crm_user_id or "").strip()
            if not oid:
                raise HTTPException(status_code=422, detail="attendee has no crm_user_id for forecast aggregation")
            recalc_scope = "self_only"
            owner_id_arg = oid

        try:
            resp = aldebaran_client.trigger_review_session_forecast_recalc(
                session_id=str(session.unique_id),
                owner_id=owner_id_arg,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Aldebaran review session recalc failed: session_id=%s err=%s",
                session_id,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Aldebaran forecast recalc failed: {e}",
            ) from e

        try:
            return _build_forecast_recalc_out_from_aldebaran(
                resp,
                recalc_scope=recalc_scope,
                session_id=str(session.unique_id),
            )
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e


crm_review_service = CRMReviewService()

