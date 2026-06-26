from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional
from uuid import UUID

from sqlmodel import Session, distinct, func, select

from app.models.crm_review import CRMReviewAttendee, CRMReviewSession
from app.repositories.department_mirror import department_mirror_repo
from app.repositories.user_department_relation import user_department_relation_repo
from app.repositories.visit_record import visit_record_repo
from app.services.oauth_service import oauth_client

REVIEW_SESSION_VIEW_PERMISSION = "review_session:all:view"


def _user_has_review_session_view_permission(user_id: UUID) -> bool:
    """OAuth POST /permission/check — review session 跨团队/全量查看权限。"""
    check = oauth_client.check_function_permission(
        user_id=user_id,
        permission=REVIEW_SESSION_VIEW_PERMISSION,
    )
    return bool(check.get("allowed"))


@dataclass(frozen=True)
class ReviewSessionViewScope:
    """
    Review session 列表/详情可见范围：
    - 普通成员：仅本人参与的 session
    - 有 review_session:all:view + 主部门：本部门及所有下属部门的 session
    - 公司管理员，或有 viewer 权限但无部门信息：全公司 session

    注意：biz:weekly_decision:view 为周决策模块入口权限，与本 viewer 权限不等价。
    """

    has_viewer_permission: bool
    is_company_admin: bool
    user_department_id: Optional[str]
    subtree_department_ids: tuple[str, ...]

    @property
    def list_filter_mode(self) -> Literal["company", "department", "attendee"]:
        if self.has_viewer_permission and (self.is_company_admin or not self.subtree_department_ids):
            return "company"
        if self.has_viewer_permission and self.subtree_department_ids:
            return "department"
        return "attendee"

    def can_access_session_as_viewer(self, session_department_id: Optional[str]) -> bool:
        if not self.has_viewer_permission:
            return False
        if self.is_company_admin or not self.subtree_department_ids:
            return True
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
    is_company_admin = visit_record_repo.can_access_all_crm_data(user_id, db_session)

    user_department_id = user_department_relation_repo.get_primary_department_by_user_ids(
        db_session,
        [str(user_id)],
    ).get(str(user_id))

    subtree_department_ids: tuple[str, ...] = ()
    if has_viewer_permission and user_department_id and not is_company_admin:
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
                .where(CRMReviewSession.department_id.in_(scope.subtree_department_ids))
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
        return stmt.where(CRMReviewSession.department_id.in_(scope.subtree_department_ids))
    return (
        stmt.join(
            CRMReviewAttendee,
            CRMReviewAttendee.session_id == CRMReviewSession.unique_id,
        )
        .where(CRMReviewAttendee.user_id == str(user_id))
    )
