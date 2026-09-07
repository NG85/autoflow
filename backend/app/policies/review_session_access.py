from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional
from uuid import UUID

from sqlmodel import Session, distinct, func, or_, select

from app.models.crm_review import CRMReviewAttendee, CRMReviewSession
from app.repositories.department_mirror import department_mirror_repo
from app.repositories.user_department_relation import user_department_relation_repo
from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client

# Wave B1：周经营决策功能门控（legacy ``review_session:all:view``）；列表范围看 data-scope
WEEKLY_DECISION_VIEW_PERMISSION = "biz:weekly_decision:view"
LEGACY_REVIEW_SESSION_VIEW_PERMISSION = "review_session:all:view"
REVIEW_SESSION_VIEW_PERMISSION = WEEKLY_DECISION_VIEW_PERMISSION
# W3 data-scope entity（data-scope-matrix：周度经营决策）
WEEKLY_DECISION_DATA_SCOPE_ENTITY = "biz_weekly_decision"
# 仅组织/汇报链可抬成部门列表；linked_crm / crm_grant / self_* 都不算
_TEAM_SOURCES = frozenset({"org_team_sub", "org_scope"})


def _filter_explicitly_enabled(item: dict[str, Any]) -> bool:
    enabled = item.get("enabled")
    return enabled is True or str(enabled).lower() == "true"


def _filter_not_disabled(item: dict[str, Any]) -> bool:
    """org_team_sub 等：缺省视为开启；仅显式 false 时跳过。"""
    enabled = item.get("enabled")
    if enabled is None:
        return True
    return enabled is True or str(enabled).lower() == "true"


def _iter_data_scope_filters(scope: dict[str, Any]) -> list[dict[str, Any]]:
    filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
    return [item for item in filters if isinstance(item, dict)]


def _user_has_review_session_view_permission(user_id: UUID) -> bool:
    """OAuth POST /permission/check — 周经营决策功能门控（SALES 也有，不代表跨部门可见）。"""
    check = oauth_client.check_function_permission(
        user_id=user_id,
        permission=REVIEW_SESSION_VIEW_PERMISSION,
    )
    return bool(check.get("allowed"))


def _get_weekly_decision_data_scope(db_session: Session, user_id: UUID) -> dict[str, Any]:
    crm_user_id = user_profile_repo.get_crm_user_id_by_user_id(db_session, user_id)
    return oauth_client.get_data_scope(
        user_id=user_id,
        crm_user_id=crm_user_id,
        entity=WEEKLY_DECISION_DATA_SCOPE_ENTITY,
    )


def _filters_have_global_scope(filters: list[dict[str, Any]]) -> bool:
    """data-scope 含 enabled ``global`` → 公司级可见。"""
    for item in filters:
        source = str(item.get("source") or "").strip()
        if source == "global" and _filter_explicitly_enabled(item):
            return True
    return False


def _filters_have_team_scope(filters: list[dict[str, Any]]) -> bool:
    """仅 ``org_team_sub`` / ``org_scope`` 抬升为部门列表（SALES_MANAGER）。

    ``linked_crm``、``crm_grant``、``self_owner`` 等与 session 部门范围无关，即使 enabled 也不抬升。
    """
    for item in filters:
        if not _filter_not_disabled(item):
            continue
        source = str(item.get("source") or "").strip()
        if source in _TEAM_SOURCES:
            return True
    return False


def _attendee_session_ids_subquery(user_id: str):
    return select(CRMReviewAttendee.session_id).where(CRMReviewAttendee.user_id == str(user_id))


def _department_or_attendee_predicate(scope: ReviewSessionViewScope, user_id: str):
    """本部门子树 session ∪ 本人参会 session（避免跨部门参会被部门过滤掉）。"""
    return or_(
        CRMReviewSession.department_id.in_(scope.subtree_department_ids),
        CRMReviewSession.unique_id.in_(_attendee_session_ids_subquery(user_id)),
    )


@dataclass(frozen=True)
class ReviewSessionViewScope:
    """
    Review session 列表/详情可见范围（``biz:weekly_decision:view`` 只做功能门控）：
    - data-scope 含 enabled ``global``：全公司 session
    - 非 global 的组织范围（``org_team_sub`` / ``org_scope``）+ 有主部门：
      本部门及下属部门 session，并保留本人参会的其它部门 session
    - ``self_owner`` / ``linked_crm`` 等，或无主部门 / 无 viewer：仅本人参会 session
    """

    has_viewer_permission: bool
    is_company_admin: bool
    user_department_id: Optional[str]
    subtree_department_ids: tuple[str, ...]

    @property
    def list_filter_mode(self) -> Literal["company", "department", "attendee"]:
        if self.has_viewer_permission and self.is_company_admin:
            return "company"
        if self.has_viewer_permission and self.subtree_department_ids:
            return "department"
        return "attendee"

    def can_access_session_as_viewer(self, session_department_id: Optional[str]) -> bool:
        if not self.has_viewer_permission:
            return False
        if self.is_company_admin:
            return True
        if not self.subtree_department_ids:
            return False
        dept_id = (session_department_id or "").strip()
        if not dept_id:
            return False
        return dept_id in self.subtree_department_ids

    def has_elevated_session_view(
        self,
        session_department_id: Optional[str],
        *,
        is_leader: bool,
    ) -> bool:
        if is_leader:
            return True
        return self.can_access_session_as_viewer(session_department_id)

    def has_full_session_data_view(
        self,
        session_department_id: Optional[str],
        *,
        is_leader: bool,
        is_attendee: bool,
    ) -> bool:
        """
        Session 内快照/分组等业务数据是否覆盖全部参会成员。
        - 负责人：全员
        - 普通参会人（非 leader）：仅本人，不因 viewer 权限放大
        - 非参会人 viewer：在可见部门范围内看全员
        """
        if is_leader:
            return True
        if is_attendee:
            return False
        return self.can_access_session_as_viewer(session_department_id)


def user_can_access_review_session(
    db_session: Session,
    *,
    user_id: UUID,
    session: CRMReviewSession,
    scope: Optional[ReviewSessionViewScope] = None,
) -> bool:
    from app.repositories.crm_review_attendee import crm_review_attendee_repo

    attendee = crm_review_attendee_repo.get_by_session_and_user_id(
        db_session,
        session_id=str(session.unique_id),
        user_id=str(user_id),
    )
    if attendee:
        return True
    resolved_scope = scope or resolve_review_session_view_scope(db_session, user_id)
    return resolved_scope.can_access_session_as_viewer(session.department_id)


def resolve_review_session_view_scope(
    db_session: Session,
    user_id: UUID,
) -> ReviewSessionViewScope:
    has_viewer_permission = _user_has_review_session_view_permission(user_id)
    filters = _iter_data_scope_filters(_get_weekly_decision_data_scope(db_session, user_id))
    is_company_admin = _filters_have_global_scope(filters)
    has_team_scope = _filters_have_team_scope(filters)

    user_department_id = user_department_relation_repo.get_primary_department_by_user_ids(
        db_session,
        [str(user_id)],
    ).get(str(user_id))

    subtree_department_ids: tuple[str, ...] = ()
    if (
        has_viewer_permission
        and has_team_scope
        and user_department_id
        and not is_company_admin
    ):
        subtree_department_ids = tuple(
            department_mirror_repo.get_subtree_department_ids(db_session, user_department_id)
        )

    return ReviewSessionViewScope(
        has_viewer_permission=has_viewer_permission,
        is_company_admin=is_company_admin,
        user_department_id=user_department_id,
        subtree_department_ids=subtree_department_ids,
    )


def get_cached_review_session_view_scope(
    db_session: Session,
    user: Any,
    cache: Optional[dict[str, ReviewSessionViewScope]] = None,
) -> ReviewSessionViewScope:
    cache_key = f"view_scope:{getattr(user, 'id', '')}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    scope = resolve_review_session_view_scope(db_session, user.id)
    if cache is not None:
        cache[cache_key] = scope
    return scope


def count_review_sessions_matching_scope(
    db_session: Session,
    scope: ReviewSessionViewScope,
    user_id: str,
) -> int:
    mode = scope.list_filter_mode
    if mode == "company":
        return int(
            db_session.exec(select(func.count()).select_from(CRMReviewSession)).one() or 0
        )
    if mode == "department":
        return int(
            db_session.exec(
                select(func.count())
                .select_from(CRMReviewSession)
                .where(_department_or_attendee_predicate(scope, user_id))
            ).one()
            or 0
        )
    return int(
        db_session.exec(
            select(func.count(distinct(CRMReviewSession.unique_id)))
            .select_from(CRMReviewSession)
            .join(
                CRMReviewAttendee,
                CRMReviewAttendee.session_id == CRMReviewSession.unique_id,
            )
            .where(CRMReviewAttendee.user_id == str(user_id))
        ).one()
        or 0
    )


def apply_review_session_list_filter(stmt, scope: ReviewSessionViewScope, user_id: str):
    mode = scope.list_filter_mode
    if mode == "company":
        return stmt
    if mode == "department":
        return stmt.where(_department_or_attendee_predicate(scope, user_id))
    return (
        stmt.join(
            CRMReviewAttendee,
            CRMReviewAttendee.session_id == CRMReviewSession.unique_id,
        )
        .where(CRMReviewAttendee.user_id == str(user_id))
    )
