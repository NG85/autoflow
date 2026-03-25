from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case
from sqlmodel import Session, select, func

from app.models.crm_review import (
    CRMReviewAttendee,
    CRMReviewOppAuditLog,
    CRMReviewOppBranchSnapshot,
    REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS,
)
from app.models.crm_system_configurations import CRMSystemConfiguration
from app.repositories.crm_review_attendee import crm_review_attendee_repo
from app.repositories.crm_review_audit import crm_review_opp_audit_log_repo
from app.repositories.crm_review_branch_snapshot import crm_review_opp_branch_snapshot_repo
from app.repositories.crm_review_session import crm_review_session_repo
from app.services.aldebaran_service import aldebaran_client

logger = logging.getLogger(__name__)


EDITABLE_FIELDS = REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS

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
    def _build_forecast_type_rank_case(db_session: Session):
        """
        Sort key for forecast_type: commit (0) > upside (1) > closed_won (2).
        Aliases come from crm_system_configurations ForecastTypeMapping (config_value JSON arrays).
        snapshot.forecast_type matches one of those strings (e.g. 确定成单 -> commit).
        """
        a0, a1, a2 = _get_forecast_type_rank_alias_tuples_cached(db_session)
        ft_col = CRMReviewOppBranchSnapshot.forecast_type
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
    def _group_field_and_label(group_by: str):
        gb = str(group_by or "owner").strip()
        if gb == "owner":
            return gb, CRMReviewOppBranchSnapshot.owner_id, CRMReviewOppBranchSnapshot.owner_name
        if gb == "forecast_type":
            return gb, CRMReviewOppBranchSnapshot.forecast_type, CRMReviewOppBranchSnapshot.forecast_type
        if gb == "opportunity_stage":
            return gb, CRMReviewOppBranchSnapshot.opportunity_stage, CRMReviewOppBranchSnapshot.opportunity_stage
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

    def _base_snapshot_stmt(self, *, owner_ids: List[str], snapshot_period: str):
        return select(CRMReviewOppBranchSnapshot).where(
            CRMReviewOppBranchSnapshot.owner_id.in_(owner_ids),
            CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
        )

    def get_my_edit_page_data(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
        page: int = 1,
        size: int = 20,
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

        total = crm_review_opp_branch_snapshot_repo.count_by_owner_ids_and_period(
            db_session,
            owner_crm_user_ids=owner_ids,
            snapshot_period=snapshot_period,
        )
        ft_rank = self._build_forecast_type_rank_case(db_session)
        items = crm_review_opp_branch_snapshot_repo.list_by_owner_ids_and_period_paginated(
            db_session,
            owner_crm_user_ids=owner_ids,
            snapshot_period=snapshot_period,
            offset=offset,
            limit=size,
            forecast_type_rank_case=ft_rank,
        )

        return {
            "session_id": str(scope["session"].unique_id),
            "page": page,
            "size": size,
            "total": total,
            "items": items,
        }

    def list_snapshot_groups(
        self,
        db_session: Session,
        *,
        session_id: str,
        user_id: str,
        group_by: str = "owner",
    ) -> dict:
        scope = self._resolve_session_scope(db_session, session_id=session_id, user_id=user_id)
        gb, field_col, label_col = self._group_field_and_label(group_by)

        stmt = (
            select(
                func.coalesce(field_col, "").label("group_key"),
                func.coalesce(func.max(label_col), "").label("group_label"),
                func.count().label("cnt"),
            )
            .where(
                CRMReviewOppBranchSnapshot.owner_id.in_(scope["owner_ids"]),
                CRMReviewOppBranchSnapshot.snapshot_period == scope["snapshot_period"],
            )
            .group_by(func.coalesce(field_col, ""))
            .order_by(func.coalesce(field_col, ""))
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
                "create_time": _to_beijing_datetime(scope["session"].create_time),
                "review_phase": scope["session"].review_phase,
            },
            "can_review": bool(scope["is_leader"]) and str(scope["session"].stage or "").strip() == "lead_view",
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
    ) -> dict:
        page = int(page or 1)
        size = int(size or 20)
        if page < 1:
            page = 1
        if size < 1:
            size = 20
        offset = (page - 1) * size

        scope = self._resolve_session_scope(db_session, session_id=session_id, user_id=user_id)
        gb, field_col, _ = self._group_field_and_label(group_by)
        group_key = str(group_key or "")

        base_where = [
            CRMReviewOppBranchSnapshot.owner_id.in_(scope["owner_ids"]),
            CRMReviewOppBranchSnapshot.snapshot_period == scope["snapshot_period"],
        ]
        if group_key == "__EMPTY__":
            base_where.append(func.coalesce(field_col, "") == "")
        else:
            base_where.append(func.coalesce(field_col, "") == group_key)

        total = int(
            db_session.exec(
                select(func.count()).select_from(CRMReviewOppBranchSnapshot).where(*base_where)
            ).one()
        )
        ft_rank = self._build_forecast_type_rank_case(db_session)
        items = db_session.exec(
            select(CRMReviewOppBranchSnapshot)
            .where(*base_where)
            .order_by(
                func.coalesce(CRMReviewOppBranchSnapshot.owner_name, ""),
                ft_rank,
                CRMReviewOppBranchSnapshot.forecast_amount.desc(),
            )
            .offset(offset)
            .limit(size)
        ).all()

        return {
            "session_id": str(scope["session"].unique_id),
            "group_by": gb,
            "group_key": group_key,
            "page": page,
            "size": size,
            "total": total,
            "items": items,
        }

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
            attendee.has_submitted = True
            attendee.submitted_at = datetime.now(timezone.utc)
            attendee.submission_count = (attendee.submission_count or 0) + 1
            db_session.add(attendee)
            db_session.commit()

            audit = CRMReviewOppAuditLog(
                unique_id=str(uuid.uuid4()),
                session_id=str(session.unique_id),
                change_scope="submit_empty_updates",
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
                changed_by=str(attendee.user_name or "unknown"),
                changed_by_id=str(user_id),
            )
            crm_review_opp_audit_log_repo.create_audit(db_session, audit)

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

        if is_leader:
            owner_crm_user_ids = crm_review_attendee_repo.get_crm_user_ids_by_session(
                db_session, session_id=session_id
            )
            rows = crm_review_opp_branch_snapshot_repo.get_by_owner_ids_period_and_snapshot_unique_ids(
                db_session,
                owner_crm_user_ids=owner_crm_user_ids,
                snapshot_period=snapshot_period,
                snapshot_unique_ids=snapshot_unique_ids,
            )
        else:
            rows = crm_review_opp_branch_snapshot_repo.get_by_owner_period_and_snapshot_unique_ids(
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
                row.last_modified_time = now
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
        # Still commit attendee submitted status + audit below even if updated_count == 0.
        if updated_count > 0:
            db_session.add_all(list(rows_by_snapshot_unique_id.values()))

        # Mark only the *actual submitter* as submitted.
        # Even when a leader edits multiple owners' snapshots, stats should count
        # only the user who pressed "submit".
        attendee.has_submitted = True
        attendee.submitted_at = datetime.now(timezone.utc)
        attendee.submission_count = (attendee.submission_count or 0) + 1
        db_session.add(attendee)

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
            unique_id=str(uuid.uuid4()),
            session_id=str(session.unique_id),
            change_scope=change_scope,
            old_value=json.dumps(
                {
                    "period": snapshot_period,
                    "attempted_updates": before,
                    "changed_snapshot_unique_ids": sorted(changed_snapshot_unique_ids),
                    "unchanged_snapshot_unique_ids": sorted(unchanged_snapshot_unique_ids),
                },
                ensure_ascii=False,
            ),
            new_value=json.dumps(
                {
                    "period": snapshot_period,
                    "attempted_updates": after,
                    "changed_snapshot_unique_ids": sorted(changed_snapshot_unique_ids),
                    "unchanged_snapshot_unique_ids": sorted(unchanged_snapshot_unique_ids),
                },
                ensure_ascii=False,
            ),
            change_type="UPDATE",
            edit_phase=str(session.stage),
            changed_by=str(attendee.user_name or "unknown"),
            changed_by_id=str(user_id),
        )
        crm_review_opp_audit_log_repo.create_audit(db_session, audit)

        submit_stats = crm_review_attendee_repo.get_submit_stats(db_session, session_id=session_id)
        return {"updated_count": updated_count, "submit_stats": submit_stats}

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

