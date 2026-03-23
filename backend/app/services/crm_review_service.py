from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models.crm_review import (
    CRMReviewAttendee,
    CRMReviewOppAuditLog,
    REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS,
)
from app.repositories.crm_review_attendee import crm_review_attendee_repo
from app.repositories.crm_review_audit import crm_review_opp_audit_log_repo
from app.repositories.crm_review_branch_snapshot import crm_review_opp_branch_snapshot_repo
from app.repositories.crm_review_session import crm_review_session_repo
from app.services.aldebaran_service import aldebaran_client

logger = logging.getLogger(__name__)


EDITABLE_FIELDS = REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS


def _parse_aldebaran_forecast_recalc_payload(body: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, float]]:
    """
    从 Aldebaran 返回体中解析与 ReviewSessionForecastRecalcOut 对齐的字段。
    支持顶层或 ``data`` 下包含 ``by_owner``、``totals_by_forecast_type``。
    """
    candidates: List[Dict[str, Any]] = []
    data = body.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    candidates.append(body)

    by_owner_raw: Any = None
    totals_raw: Any = None
    for node in candidates:
        if not isinstance(node, dict):
            continue
        bo = node.get("by_owner")
        tt = node.get("totals_by_forecast_type")
        if isinstance(bo, list) and isinstance(tt, dict):
            by_owner_raw = bo
            totals_raw = tt
            break

    if by_owner_raw is None or totals_raw is None:
        raise ValueError(
            "Aldebaran forecast recalc response must include by_owner (array) and "
            "totals_by_forecast_type (object), under root or under data"
        )

    by_owner_list: List[Dict[str, Any]] = []
    for item in by_owner_raw:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("owner_id", "") or "").strip()
        raw_ft = item.get("by_forecast_type") or {}
        if not isinstance(raw_ft, dict):
            raw_ft = {}
        by_ft: Dict[str, float] = {}
        for k, v in raw_ft.items():
            try:
                by_ft[str(k)] = float(v)
            except (TypeError, ValueError):
                by_ft[str(k)] = 0.0
        by_owner_list.append(
            {
                "owner_id": oid,
                "owner_name": str(item.get("owner_name", "") or ""),
                "by_forecast_type": dict(sorted(by_ft.items(), key=lambda x: x[0])),
            }
        )

    totals: Dict[str, float] = {}
    for k, v in totals_raw.items():
        try:
            totals[str(k)] = float(v)
        except (TypeError, ValueError):
            totals[str(k)] = 0.0

    return by_owner_list, dict(sorted(totals.items(), key=lambda x: x[0]))


class CRMReviewService:
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
        session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="review session not found")

        attendee = crm_review_attendee_repo.get_by_session_and_user_id(
            db_session, session_id=session_id, user_id=user_id
        )
        if not attendee:
            raise HTTPException(status_code=403, detail="user is not attendee of this review session")

        snapshot_period = session.period
        owner_crm_user_id = attendee.crm_user_id

        is_leader = bool(getattr(attendee, "is_leader", False))
        if is_leader:
            owner_crm_user_ids = crm_review_attendee_repo.get_crm_user_ids_by_session(
                db_session, session_id=session_id
            )
            total = crm_review_opp_branch_snapshot_repo.count_by_owner_ids_and_period(
                db_session,
                owner_crm_user_ids=owner_crm_user_ids,
                snapshot_period=snapshot_period,
            )
            items = crm_review_opp_branch_snapshot_repo.list_by_owner_ids_and_period_paginated(
                db_session,
                owner_crm_user_ids=owner_crm_user_ids,
                snapshot_period=snapshot_period,
                offset=offset,
                limit=size,
            )
        else:
            total = crm_review_opp_branch_snapshot_repo.count_by_owner_and_period(
                db_session,
                owner_crm_user_id=owner_crm_user_id,
                snapshot_period=snapshot_period,
            )
            items = crm_review_opp_branch_snapshot_repo.list_by_owner_and_period_paginated(
                db_session,
                owner_crm_user_id=owner_crm_user_id,
                snapshot_period=snapshot_period,
                offset=offset,
                limit=size,
            )

        submit_stats = crm_review_attendee_repo.get_submit_stats(db_session, session_id=session_id)

        editable = bool(
            session.stage == "initial_edit"
            or (session.stage == "lead_review" and session.review_phase == "edit")
        )

        return {
            "session": {
                "session_id": session.unique_id,
                "period": session.period,
                "stage": session.stage,
                "review_phase": session.review_phase,
            },
            "is_leader": is_leader,
            "editable": editable,
            "submit_stats": submit_stats,
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
        forecast 聚合仅以 Aldebaran 返回为准，不做本地 DB 汇总兜底。
        - Leader：``recalc_scope=full_session``（全量）。
        - 普通参会人：``recalc_scope=self_only``，并传 ``owner_id=crm_user_id``。
        """
        session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="review session not found")

        attendee = crm_review_attendee_repo.get_by_session_and_user_id(
            db_session, session_id=session_id, user_id=user_id
        )
        if not attendee:
            raise HTTPException(status_code=403, detail="user is not attendee of this review session")

        period = str(session.period or "").strip()
        if not period:
            raise HTTPException(status_code=500, detail="review session period is empty")

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
                period=period,
                recalc_scope=recalc_scope,
                owner_id=owner_id_arg,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Aldebaran review session recalc failed: session_id=%s period=%s err=%s",
                session_id,
                period,
                e,
                exc_info=True,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Aldebaran forecast recalc failed: {e}",
            ) from e

        try:
            by_owner_list, totals = _parse_aldebaran_forecast_recalc_payload(resp)
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

        return {
            "session_id": str(session.unique_id),
            "period": period,
            "recalc_scope": recalc_scope,
            "aldebaran_invoked": True,
            "aldebaran_response": resp,
            "aldebaran_error": None,
            "by_owner": by_owner_list,
            "totals_by_forecast_type": totals,
        }


crm_review_service = CRMReviewService()

