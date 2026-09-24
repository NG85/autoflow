"""拜访复盘查询：按当前用户选视角，销售视角再附结构化抽取。

洞察来自 crm_entity_insight，抽取来自 crm_postvisit_extract_items。
没有复盘行时不查汇报链、不读抽取表。上级视角不返回抽取。
任一侧读取失败时该侧为空，不把异常抛给调用方。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlmodel import Session

from app.repositories.user_profile import UserProfileRepo
from app.repositories.visit_record import visit_record_repo
from app.services.visit_record_extract_reader import (
    empty_extract_response,
    load_visit_record_extract_response,
)
from app.services.visit_record_insight_reader import (
    RECAP_VIEW_SALES,
    VisitRecordInsight,
    collect_profile_open_ids,
    load_visit_record_insights_by_view,
    recap_view_for_viewer,
    resolve_visit_record_detail_recap,
    viewer_is_leader_of_recorder,
    viewer_is_recorder,
)

logger = logging.getLogger(__name__)


def empty_recap_response(visit_id: str) -> dict[str, Any]:
    return {
        "visit_id": visit_id,
        "view": None,
        "insight": None,
        "extract": empty_extract_response(visit_id),
    }


def resolve_viewer_recap(
    session: Session,
    *,
    visit_record_id: str,
    recorder_id: Any,
    viewer_user_id: Any,
) -> Optional[tuple[str, Optional[VisitRecordInsight]]]:
    """有复盘才按当前用户选视角。没有复盘返回 None，且不会查汇报链。"""
    insights = load_visit_record_insights_by_view(session, visit_record_id)

    def _resolve_non_recorder_recap_view() -> str:
        viewer_profile = UserProfileRepo().get_by_user_id(session, viewer_user_id)
        recorder_profile = (
            UserProfileRepo().get_by_recorder_id(session, str(recorder_id))
            if recorder_id
            else None
        )
        viewer_oauth_user_id = (
            getattr(viewer_profile, "oauth_user_id", None) if viewer_profile else None
        )
        viewer_open_ids = collect_profile_open_ids(viewer_profile)
        reporting_chain_leader_open_ids: list[str] = []
        base_user_id = None
        if recorder_profile is not None:
            if getattr(recorder_profile, "user_id", None):
                base_user_id = str(recorder_profile.user_id)
            else:
                for account in getattr(recorder_profile, "oauth_users", None) or []:
                    if getattr(account, "user_id", None):
                        base_user_id = str(account.user_id)
                        break
        if base_user_id:
            try:
                from app.services.oauth_service import oauth_client

                reporting_chain_leader_open_ids = [
                    str(leader.get("open_id")).strip()
                    for leader in oauth_client.get_reporting_chain_leaders(
                        base_user_id=base_user_id,
                        max_levels=1,
                    )
                    if leader.get("open_id")
                ]
            except Exception as chain_error:
                logger.warning(
                    "拜访复盘详情查询汇报链失败: record_id=%s error=%s",
                    visit_record_id,
                    chain_error,
                )
        return recap_view_for_viewer(
            viewer_user_id=viewer_user_id,
            recorder_id=recorder_id,
            viewer_oauth_user_id=viewer_oauth_user_id,
            collaborative_participants=visit_record_repo.get_collaborative_participants_raw(
                session, visit_record_id
            ),
            is_leader=viewer_is_leader_of_recorder(
                viewer_user_id=viewer_user_id,
                viewer_oauth_user_id=viewer_oauth_user_id,
                viewer_open_ids=viewer_open_ids,
                recorder_direct_manager_id=(
                    getattr(recorder_profile, "direct_manager_id", None)
                    if recorder_profile
                    else None
                ),
                reporting_chain_leader_open_ids=reporting_chain_leader_open_ids,
            ),
        )

    return resolve_visit_record_detail_recap(
        insights,
        viewer_is_recorder=viewer_is_recorder(viewer_user_id, recorder_id),
        resolve_non_recorder_view=_resolve_non_recorder_recap_view,
    )


def load_visit_record_recap_response(
    session: Session,
    *,
    visit_record_id: str,
    recorder_id: Any,
    viewer_user_id: Any,
) -> dict[str, Any]:
    """当前用户的复盘。销售视角附带 items 与 card_links；其余情况抽取为空。"""
    rid = (visit_record_id or "").strip()
    empty = empty_recap_response(rid)
    try:
        resolved = resolve_viewer_recap(
            session,
            visit_record_id=rid,
            recorder_id=recorder_id,
            viewer_user_id=viewer_user_id,
        )
    except Exception as exc:
        logger.warning("加载拜访复盘失败: record_id=%s error=%s", rid, exc)
        return empty
    if not resolved:
        return empty
    view, picked = resolved
    extract = empty_extract_response(rid)
    if view == RECAP_VIEW_SALES:
        extract = load_visit_record_extract_response(session, rid)
    return {
        "visit_id": rid,
        "view": view,
        "insight": picked.to_public_dict() if picked else None,
        "extract": extract,
    }
