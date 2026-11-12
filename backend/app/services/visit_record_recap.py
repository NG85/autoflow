"""拜访复盘查询：按当前用户选视角，记录人再附结构化抽取。

洞察来自 crm_entity_insight，抽取来自 crm_postvisit_extract_items。
复盘和表现评价都没有时不查汇报链、不读抽取表。
能看这条拜访的人，以及这条拜访的个人收件人，可以打开复盘页。能看页面的人不因此收到卡片。
个人收件人是记录人、直接汇报上级、抄送和协同人。部门群没有个人名单，群里点开仍要能看这条拜访。
记录人看销售视角，并附带本人的人员表现评价和结构化抽取。
协同人看销售视角的业务推进和人员表现评价，抽取为空。抄送看上级视角的业务推进。
其他能看这条拜访的人看上级视角的业务推进，抽取为空。
上级视角的人员表现评价只给直接汇报上级和公司级管理人员（biz_admin、sys_admin、executive）。
只有这份评价、没有业务推进时，其他人打不开页面，也不查到评价正文。
能打开时 tabs 只有 key 和标题。insight 是业务推进报告，performance 是人员表现评价报告。任一侧读取失败时该侧为空，不把异常抛给调用方。
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
    RECAP_PAGE_TABS,
    VisitRecordInsight,
    collect_profile_open_ids,
    has_visit_recap_page_insight,
    PERFORMANCE_VIEW_LEADER,
    PERFORMANCE_VIEW_SALES,
    RECAP_VIEW_LEADER,
    RECAP_VIEW_SALES,
    load_visit_record_insights_by_view,
    viewer_is_leader_of_recorder,
    viewer_is_recorder,
)

logger = logging.getLogger(__name__)


def empty_recap_response(visit_id: str) -> dict[str, Any]:
    return {
        "visit_id": visit_id,
        "view": None,
        "insight": None,
        "performance": None,
        "tabs": [],
        "extract": empty_extract_response(visit_id),
    }


def build_recap_page_tabs() -> list[dict[str, str]]:
    """能打开复盘页时的两个 tab。只给 key 和标题，报告在 insight 与 performance。"""
    return [{"key": key, "title": title} for key, title in RECAP_PAGE_TABS]


class RecapViewContext:
    """按记录人缓存汇报链结果。记录人本人不查链。"""

    def __init__(self, session: Session, viewer_user_id: Any) -> None:
        self.session = session
        self.viewer_user_id = viewer_user_id
        self._leader_by_recorder: dict[str, bool] = {}
        self._viewer_profile_row: Any = None
        self._viewer_profile_loaded = False
        self._company_manager: Optional[bool] = None
        self._cc_by_recorder: dict[str, bool] = {}

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

    def is_company_manager(self) -> bool:
        """当前用户是否为公司级管理人员。查一次，失败视为不是。"""
        if self._company_manager is None:
            self._company_manager = _viewer_is_company_manager(self.viewer_user_id)
        return self._company_manager

    def is_collaborative_participant(self, collaborative_participants: Any) -> bool:
        """协同人按 ask_id 对齐当前用户的 user id、oauth id 或 open_id。"""
        from app.utils.participants_utils import parse_collaborative_participants_list

        ask_ids = [
            item.get("ask_id")
            for item in parse_collaborative_participants_list(collaborative_participants)
            if isinstance(item, dict) and item.get("ask_id")
        ]
        if not ask_ids or not self.viewer_user_id:
            return False
        profile = self._viewer_profile()
        return viewer_is_recorder(
            [
                self.viewer_user_id,
                getattr(profile, "oauth_user_id", None) if profile else None,
                collect_profile_open_ids(profile),
            ],
            ask_ids,
        )

    def is_configured_cc(self, recorder_id: Any, recorder_department_id: Any = None) -> bool:
        """抄送规则里的个人收件人。查一次，失败视为不是。"""
        key = str(recorder_id or "")
        if key not in self._cc_by_recorder:
            self._cc_by_recorder[key] = self._query_configured_cc(recorder_id, recorder_department_id)
        return self._cc_by_recorder[key]

    def can_see_leader_performance(self, recorder_id: Any) -> bool:
        """上级视角的人员表现评价：直接汇报上级或公司级管理人员。记录人走销售视角。"""
        if not self.viewer_user_id or self.is_recorder(recorder_id):
            return False
        if self.is_company_manager():
            return True
        return self.is_reporting_leader(recorder_id)

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

    def _query_configured_cc(self, recorder_id: Any, recorder_department_id: Any) -> bool:
        from uuid import UUID

        from app.services.visit_record_cc_resolver import resolve_visit_record_cc_recipients

        raw = str(recorder_id or "").strip()
        recorder_user_id = None
        if raw:
            try:
                recorder_user_id = UUID(raw)
            except ValueError:
                if len(raw) == 32:
                    try:
                        recorder_user_id = UUID(hex=raw)
                    except ValueError:
                        recorder_user_id = None
        if recorder_user_id is None:
            return False
        try:
            grouped = resolve_visit_record_cc_recipients(
                self.session,
                recorder_user_id=recorder_user_id,
                recorder_department_id=str(recorder_department_id or "") or None,
            )
        except Exception as exc:
            logger.warning("拜访复盘查询抄送收件人失败 recorder=%s: %s", raw, exc)
            return False
        open_ids = [
            item.get("open_id")
            for recipients in grouped.values()
            for item in recipients
            if isinstance(item, dict) and item.get("open_id")
        ]
        if not open_ids:
            return False
        profile = self._viewer_profile()
        return viewer_is_recorder(
            [
                self.viewer_user_id,
                getattr(profile, "oauth_user_id", None) if profile else None,
                collect_profile_open_ids(profile),
            ],
            open_ids,
        )


def _viewer_is_company_manager(user_id: Any) -> bool:
    """公司级管理人员可看上级视角复盘：biz_admin、sys_admin、executive。角色查询失败时不放开。"""
    from app.services.leader_meeting_review_actions import viewer_is_company_manager

    return viewer_is_company_manager(user_id)


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
    is_collaborative_participant: bool = False,
    is_configured_cc: bool = False,
    can_see_visit: bool = True,
) -> Optional[tuple[str, Optional[VisitRecordInsight], Optional[VisitRecordInsight]]]:
    """调用方已经能打开页面。没有任何报告时返回 None，且不会查汇报链。

    没有上级视角人员表现评价时，也不查角色和汇报链。
    协同人看销售视角。看不到这条拜访的协同人看不到上级视角。
    抄送只看上级视角的业务推进，不看人员表现评价。
    """
    insights = load_visit_record_insights_by_view(session, visit_record_id)
    if not has_visit_recap_page_insight(insights):
        return None
    context = RecapViewContext(session, viewer_user_id)
    if context.is_recorder(recorder_id):
        business = insights.get(RECAP_VIEW_SALES)
        performance = insights.get(PERFORMANCE_VIEW_SALES)
        if not business and not performance:
            return None
        return RECAP_VIEW_SALES, business, performance
    leader_business = insights.get(RECAP_VIEW_LEADER)
    leader_performance = insights.get(PERFORMANCE_VIEW_LEADER)
    if leader_performance and context.can_see_leader_performance(recorder_id):
        return RECAP_VIEW_LEADER, leader_business, leader_performance
    if is_collaborative_participant and not is_configured_cc:
        business = insights.get(RECAP_VIEW_SALES)
        performance = insights.get(PERFORMANCE_VIEW_SALES)
        if business or performance:
            return RECAP_VIEW_SALES, business, performance
        if not can_see_visit:
            return None
    if not leader_business:
        return None
    return RECAP_VIEW_LEADER, leader_business, None


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
    is_collaborative_participant: bool = False,
    is_configured_cc: bool = False,
    can_see_visit: bool = True,
) -> dict[str, Any]:
    """当前用户的复盘页。记录人附带 items 与 card_links；其他人抽取为空。"""
    rid = (visit_record_id or "").strip()
    empty = empty_recap_response(rid)
    try:
        resolved = resolve_viewer_recap(
            session,
            visit_record_id=rid,
            recorder_id=recorder_id,
            viewer_user_id=viewer_user_id,
            is_collaborative_participant=is_collaborative_participant,
            is_configured_cc=is_configured_cc,
            can_see_visit=can_see_visit,
        )
    except Exception as exc:
        logger.warning("加载拜访复盘失败: record_id=%s error=%s", rid, exc)
        return empty
    if not resolved:
        return empty
    view, recap, performance = resolved
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
        "insight": recap.to_public_dict() if recap else None,
        "performance": performance.to_public_dict() if performance else None,
        "tabs": build_recap_page_tabs(),
        "extract": extract,
    }
