"""拜访复盘查询：按当前用户选视角，记录人再附结构化抽取。

洞察来自 crm_entity_insight，抽取来自 crm_postvisit_extract_items。
没有复盘行时不查汇报链、不读抽取表。
记录人看销售视角，其汇报链上级看上级视角。系统管理员看上级视角，不附带抽取。其余人没有可看的复盘。
抽取仅记录人本人可见。任一侧读取失败时该侧为空，不把异常抛给调用方。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlmodel import Session

from app.repositories.user_profile import UserProfileRepo
from app.services.visit_record_extract_reader import (
    empty_extract_response,
    load_visit_record_extract_response,
)
from app.services.visit_record_insight_reader import (
    VisitRecordInsight,
    collect_profile_open_ids,
    has_visit_recap_insight,
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


class RecapViewContext:
    """按记录人缓存汇报链结果。记录人本人不查链。"""

    def __init__(self, session: Session, viewer_user_id: Any) -> None:
        self.session = session
        self.viewer_user_id = viewer_user_id
        self._leader_by_recorder: dict[str, bool] = {}
        self._viewer_profile_row: Any = None
        self._viewer_profile_loaded = False
        self._sys_admin: Optional[bool] = None

    def is_recorder(self, recorder_id: Any) -> bool:
        return viewer_is_recorder(self.viewer_user_id, recorder_id)

    def is_reporting_leader(self, recorder_id: Any) -> bool:
        """记录人的汇报链上级。记录人本人不查链。"""
        if not self.viewer_user_id or self.is_recorder(recorder_id):
            return False
        key = str(recorder_id or "")
        if key not in self._leader_by_recorder:
            self._leader_by_recorder[key] = self._query_reporting_leader(recorder_id)
        return self._leader_by_recorder[key]

    def is_sys_admin(self) -> bool:
        """当前用户是否为系统管理员。查一次，失败视为不是。"""
        if self._sys_admin is None:
            self._sys_admin = _viewer_is_sys_admin(self.viewer_user_id)
        return self._sys_admin

    def view_for(self, recorder_id: Any) -> Optional[str]:
        if self.is_recorder(recorder_id):
            return recap_view_for_viewer(is_recorder=True)
        if self.is_sys_admin():
            return recap_view_for_viewer(is_sys_admin=True)
        return recap_view_for_viewer(is_leader=self.is_reporting_leader(recorder_id))

    def _viewer_profile(self) -> Any:
        if not self._viewer_profile_loaded:
            self._viewer_profile_loaded = True
            if not self.viewer_user_id:
                self._viewer_profile_row = None
            else:
                try:
                    self._viewer_profile_row = UserProfileRepo().get_by_user_id(
                        self.session, self.viewer_user_id
                    )
                except Exception as exc:
                    logger.warning("拜访复盘读取当前用户档案失败: %s", exc)
                    self._viewer_profile_row = None
        return self._viewer_profile_row

    def _query_reporting_leader(self, recorder_id: Any) -> bool:
        viewer_profile = self._viewer_profile()
        recorder_profile = None
        if recorder_id:
            try:
                recorder_profile = UserProfileRepo().get_by_recorder_id(
                    self.session, str(recorder_id)
                )
            except Exception as exc:
                logger.warning("拜访复盘读取记录人档案失败: %s", exc)
        viewer_oauth_user_id = (
            getattr(viewer_profile, "oauth_user_id", None) if viewer_profile else None
        )
        reporting_chain_leader_open_ids = _reporting_chain_leader_open_ids(recorder_profile)
        return viewer_is_leader_of_recorder(
            viewer_user_id=self.viewer_user_id,
            viewer_oauth_user_id=viewer_oauth_user_id,
            viewer_open_ids=collect_profile_open_ids(viewer_profile),
            recorder_direct_manager_id=(
                getattr(recorder_profile, "direct_manager_id", None)
                if recorder_profile
                else None
            ),
            reporting_chain_leader_open_ids=reporting_chain_leader_open_ids,
        )


def _viewer_is_sys_admin(user_id: Any) -> bool:
    """系统管理员可看上级视角复盘。角色查询失败时不放开。"""
    from uuid import UUID

    if isinstance(user_id, UUID):
        uid = user_id
    else:
        try:
            uid = UUID(str(user_id)) if user_id else None
        except (TypeError, ValueError):
            return False
    if uid is None:
        return False
    try:
        from app.services.oauth_service import oauth_client

        return oauth_client.check_user_has_role(user_id=uid, role="SYS_ADMIN") is True
    except Exception as exc:
        logger.warning("拜访复盘查询系统管理员角色失败: %s", exc)
        return False


def _reporting_chain_leader_open_ids(recorder_profile: Any) -> list[str]:
    base_user_id = None
    if recorder_profile is not None:
        if getattr(recorder_profile, "user_id", None):
            base_user_id = str(recorder_profile.user_id)
        else:
            for account in getattr(recorder_profile, "oauth_users", None) or []:
                if getattr(account, "user_id", None):
                    base_user_id = str(account.user_id)
                    break
    if not base_user_id:
        return []
    try:
        from app.services.oauth_service import oauth_client

        return [
            str(leader.get("open_id")).strip()
            for leader in oauth_client.get_reporting_chain_leaders(
                base_user_id=base_user_id,
                max_levels=1,
            )
            if leader.get("open_id")
        ]
    except Exception as chain_error:
        logger.warning("拜访复盘查询汇报链失败: %s", chain_error)
        return []


def resolve_viewer_recap(
    session: Session,
    *,
    visit_record_id: str,
    recorder_id: Any,
    viewer_user_id: Any,
) -> Optional[tuple[str, Optional[VisitRecordInsight]]]:
    """有复盘才按当前用户选视角。没有复盘返回 None，且不会查汇报链。"""
    insights = load_visit_record_insights_by_view(session, visit_record_id)
    if not has_visit_recap_insight(insights):
        return None
    context = RecapViewContext(session, viewer_user_id)
    return resolve_visit_record_detail_recap(
        insights,
        resolve_view=lambda: context.view_for(recorder_id),
    )


def viewer_can_see_extract(
    viewer_user_id: Any,
    recorder_id: Any,
) -> bool:
    """结构化抽取（含赞踩/采纳）仅记录人本人可看、可写。"""
    return viewer_is_recorder(viewer_user_id, recorder_id)


def load_visit_record_recap_response(
    session: Session,
    *,
    visit_record_id: str,
    recorder_id: Any,
    viewer_user_id: Any,
) -> dict[str, Any]:
    """当前用户的复盘。记录人附带 items 与 card_links；其他人抽取为空。"""
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
    if viewer_can_see_extract(viewer_user_id, recorder_id):
        extract = load_visit_record_extract_response(
            session,
            rid,
            viewer_user_id=viewer_user_id,
        )
    return {
        "visit_id": rid,
        "view": view,
        "insight": picked.to_public_dict() if picked else None,
        "extract": extract,
    }
