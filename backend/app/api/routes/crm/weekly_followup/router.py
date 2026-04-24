"""CRM 周跟进 HTTP 路由。"""
import logging
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote_plus
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException
from sqlmodel import distinct, func, or_, select

from app.api.deps import CurrentUserDep, SessionDep
from app.api.routes.crm.models import (
    CRMComment,
    SaveWeeklyFollowupCommentsIn,
    WeeklyFollowupDetailOut,
    WeeklyFollowupDetailQueryIn,
    WeeklyFollowupEntityPageOut,
    WeeklyFollowupEntityRowOut,
    WeeklyFollowupFilterOptionsOut,
    WeeklyFollowupFilterOptionsQueryIn,
    WeeklyFollowupLeaderEngagementOut,
    WeeklyFollowupReviewStatusOut,
    WeeklyFollowupSummaryItemOut,
    WeeklyFollowupTriggerTaskIn,
    WeeklyFollowupTriggerTaskOut,
    WeeklyFollowupWeeklyListItemOut,
    WeeklyFollowupWeeklyListOut,
    WeeklyFollowupWeeklyListQueryIn,
)
from app.models.crm_weekly_followup_entity_summary import CRMWeeklyFollowupEntitySummary
from app.models.crm_weekly_followup_leader_engagement import CRMWeeklyFollowupLeaderEngagement
from app.models.crm_weekly_followup_summary import CRMWeeklyFollowupSummary
from app.repositories.department_mirror import department_mirror_repo
from app.repositories.user_department_relation import user_department_relation_repo
from app.repositories.user_profile import UserProfileRepo
from app.repositories.visit_record import visit_record_repo
from app.services.crm_weekly_followup_engagement_service import crm_weekly_followup_engagement_service
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/weekly-followup"])


def _can_view_weekly_followup(db_session: SessionDep, user: CurrentUserDep) -> tuple[bool, bool, Optional[str], Optional[str]]:
    """
    Returns: (can_view, is_company_admin, user_department_id, user_department_name)
    """
    user_profile_repo = UserProfileRepo()
    profile = user_profile_repo.get_by_user_id(db_session, user.id)
    dept_name = profile.department if profile else None

    # 部门信息优先从 user_department_relation 获取（更权威）；拿不到再兜底 profiles
    dept_id = user_department_relation_repo.get_primary_department_by_user_ids(
        db_session,
        [str(user.id)],
    ).get(str(user.id))

    roles_and_permissions = oauth_client.query_user_roles_and_permissions(user_id=user.id)
    permissions = roles_and_permissions.get("permissions", []) if isinstance(roles_and_permissions, dict) else []

    is_company_admin = visit_record_repo._is_admin_user(user.id, db_session, permissions)
    # leader 判定：优先使用 user_department_relation.is_leader；兜底再用 profiles 的“无直属上级”口径
    is_leader_flag = user_department_relation_repo.get_is_leader_by_user_ids(
        db_session,
        [str(user.id)],
    ).get(str(user.id))
    if is_leader_flag is None:
        is_team_lead = bool(profile and profile.department and not profile.direct_manager_id)
    else:
        is_team_lead = bool(is_leader_flag)
    has_dept_view = bool("report51:dept:view" in permissions)

    can_view = bool(is_company_admin or is_team_lead or has_dept_view or user.is_superuser)
    return can_view, bool(is_company_admin or user.is_superuser), dept_id, dept_name


def _can_edit_weekly_followup_comments(db_session: SessionDep, user: CurrentUserDep) -> tuple[bool, bool, Optional[str], Optional[str]]:
    """
    评论编辑权限：仅团队负责人或公司管理员
    Returns: (can_edit, is_company_admin, user_department_id, user_department_name)
    """
    can_view, is_company_admin, dept_id, dept_name = _can_view_weekly_followup(db_session, user)
    if is_company_admin:
        return True, True, dept_id, dept_name

    # 仅 leader 可以编辑评论（普通销售不可编辑）
    is_leader_flag = user_department_relation_repo.get_is_leader_by_user_ids(
        db_session,
        [str(user.id)],
    ).get(str(user.id))
    if is_leader_flag is True:
        return True, False, dept_id, dept_name

    # fallback：如果缺少 relation 数据，沿用 profiles 的 leader 口径
    user_profile_repo = UserProfileRepo()
    profile = user_profile_repo.get_by_user_id(db_session, user.id)
    is_team_lead_fallback = bool(profile and profile.department and not profile.direct_manager_id)
    return bool(is_team_lead_fallback), False, dept_id, dept_name


def _to_comments(v: object) -> list[CRMComment]:
    if not isinstance(v, list):
        return []
    out: list[CRMComment] = []
    for item in v:
        if not isinstance(item, dict):
            continue
        try:
            created_at_raw = item.get("created_at")
            created_at = None
            if created_at_raw:
                created_at = datetime.fromisoformat(str(created_at_raw))
            out.append(
                CRMComment(
                    author_id=str(item.get("author_id") or ""),
                    author=str(item.get("author") or ""),
                    content=str(item.get("content") or ""),
                    type=str(item.get("type") or ""),
                    created_at=created_at,
                )
            )
        except Exception:
            continue
    return out
@router.post("/crm/weekly-followup/detail")
def get_weekly_followup_detail(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: WeeklyFollowupDetailQueryIn,
) -> WeeklyFollowupDetailOut:
    """
    查询单次周总结详情（整体总结 + scope 下实体明细列表）
    """
    can_view_team, is_company_admin, user_dept_id, user_dept_name = _can_view_weekly_followup(db_session, user)

    scope = payload.scope
    if scope == "company" and not is_company_admin:
        raise HTTPException(status_code=403, detail="权限不足：仅公司管理员可查看 company scope")
    # department scope：团队负责人/管理员可看全团队；普通销售允许访问，但仅返回“自己负责”的明细行

    week_start = payload.start_date
    week_end = payload.end_date
    # 详情页明细列表需要完整展示（包含评论）
    include_comments = True
    is_sales_limited = bool(scope == "department" and (not is_company_admin) and (not can_view_team))

    page = max(int(payload.page or 1), 1)
    size = max(min(int(payload.size or 50), 200), 1)
    offset = (page - 1) * size

    # 解析部门过滤（仅 department scope）
    dept_id = None
    dept_name = None
    # 本部门 + 所有子部门的 department_id 列表，用于匹配 summary/entities（含子部门数据）
    subtree_dept_ids: List[str] = []
    if scope == "department":
        if is_company_admin:
            dept_id = (payload.department_id or "").strip() or None
            dept_name = (payload.department_name or "").strip() or None
            if dept_id is None and dept_name is None:
                raise HTTPException(status_code=400, detail="department scope 需要指定 department_id 或 department_name")
        else:
            dept_id = user_dept_id
            dept_name = user_dept_name
            if dept_id is None and dept_name is None:
                raise HTTPException(status_code=403, detail="无法获取本团队信息")
        if dept_id:
            subtree_dept_ids = department_mirror_repo.get_subtree_department_ids(db_session, dept_id)
        elif dept_name:
            ids_with_name = department_mirror_repo.get_department_ids_by_name(db_session, dept_name)
            seen: set[str] = set()
            for did in ids_with_name:
                for sid in department_mirror_repo.get_subtree_department_ids(db_session, did):
                    if sid not in seen:
                        seen.add(sid)
                        subtree_dept_ids.append(sid)

    # summary（company/department）
    summary_out: Optional[WeeklyFollowupSummaryItemOut] = None
    if scope in {"company", "department"}:
        stmt = select(CRMWeeklyFollowupSummary).where(
            CRMWeeklyFollowupSummary.week_start == week_start,
            CRMWeeklyFollowupSummary.week_end == week_end,
            CRMWeeklyFollowupSummary.summary_type == ("company" if scope == "company" else "department"),
        )
        if scope == "company":
            stmt = stmt.where(CRMWeeklyFollowupSummary.department_name == "")
        else:
            # summary 只取当前选中部门的一条
            if dept_id:
                stmt = stmt.where(CRMWeeklyFollowupSummary.department_id == dept_id)
            elif dept_name:
                stmt = stmt.where(CRMWeeklyFollowupSummary.department_name == dept_name)

        s = db_session.exec(stmt).first()
        if s:
            # 普通销售查看 department scope：不返回团队整体 summary_content，避免泄露团队其他成员信息
            summary_content = (s.summary_content or "") if not is_sales_limited else ""
            summary_out = WeeklyFollowupSummaryItemOut(
                id=s.id,
                week_start=s.week_start,
                week_end=s.week_end,
                summary_type=s.summary_type,
                department_id=s.department_id,
                department_name=s.department_name,
                title=s.title or "",
                summary_content=summary_content,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )

    # entities list
    conds = [
        CRMWeeklyFollowupEntitySummary.week_start == week_start,
        CRMWeeklyFollowupEntitySummary.week_end == week_end,
    ]
    if scope == "my":
        conds.append(CRMWeeklyFollowupEntitySummary.owner_user_id == str(user.id))
    elif scope == "department":
        # 匹配本部门及所有子部门的实体
        if subtree_dept_ids:
            conds.append(CRMWeeklyFollowupEntitySummary.department_id.in_(subtree_dept_ids))
        elif dept_id:
            conds.append(CRMWeeklyFollowupEntitySummary.department_id == dept_id)
        elif dept_name:
            conds.append(CRMWeeklyFollowupEntitySummary.department_name == dept_name)
        if is_sales_limited:
            # 普通销售：只能看自己负责的商机/客户明细
            conds.append(CRMWeeklyFollowupEntitySummary.owner_user_id == str(user.id))
    
    # 添加筛选条件（支持多选）
    if payload.filter_department_name:
        # 过滤空字符串并去重
        filter_dept_names = list(set([name.strip() for name in payload.filter_department_name if name and name.strip()]))
        if filter_dept_names:
            conds.append(CRMWeeklyFollowupEntitySummary.department_name.in_(filter_dept_names))
    
    if payload.filter_owner_name:
        # 过滤空字符串并去重
        filter_owner_names = list(set([name.strip() for name in payload.filter_owner_name if name and name.strip()]))
        if filter_owner_names:
            conds.append(CRMWeeklyFollowupEntitySummary.owner_name.in_(filter_owner_names))
    
    # Account 筛选（支持 id 或 name，任一匹配即可）
    account_conds = []
    if payload.filter_account_id:
        filter_account_id = payload.filter_account_id.strip()
        if filter_account_id:
            account_conds.append(CRMWeeklyFollowupEntitySummary.account_id == filter_account_id)
    
    if payload.filter_account_name:
        filter_account_name = payload.filter_account_name.strip()
        if filter_account_name:
            account_conds.append(CRMWeeklyFollowupEntitySummary.account_name == filter_account_name)
    
    if account_conds:
        # 如果同时提供了 id 和 name，使用 OR 逻辑（匹配任一即可）
        if len(account_conds) > 1:
            conds.append(or_(*account_conds))
        else:
            conds.append(account_conds[0])
    
    # Opportunity 筛选（支持 id 或 name，任一匹配即可）
    opportunity_conds = []
    if payload.filter_opportunity_id:
        filter_opportunity_id = payload.filter_opportunity_id.strip()
        if filter_opportunity_id:
            opportunity_conds.append(CRMWeeklyFollowupEntitySummary.opportunity_id == filter_opportunity_id)
    
    if payload.filter_opportunity_name:
        filter_opportunity_name = payload.filter_opportunity_name.strip()
        if filter_opportunity_name:
            opportunity_conds.append(CRMWeeklyFollowupEntitySummary.opportunity_name == filter_opportunity_name)
    
    if opportunity_conds:
        # 如果同时提供了 id 和 name，使用 OR 逻辑（匹配任一即可）
        if len(opportunity_conds) > 1:
            conds.append(or_(*opportunity_conds))
        else:
            conds.append(opportunity_conds[0])

    total = db_session.exec(select(func.count()).select_from(CRMWeeklyFollowupEntitySummary).where(*conds)).one()
    entities = db_session.exec(
        select(CRMWeeklyFollowupEntitySummary)
        .where(*conds)
        .order_by(CRMWeeklyFollowupEntitySummary.department_name, CRMWeeklyFollowupEntitySummary.owner_name, CRMWeeklyFollowupEntitySummary.updated_at.desc())
        .offset(offset)
        .limit(size)
    ).all()

    items = [
        WeeklyFollowupEntityRowOut(
            id=e.id,
            department_name=e.department_name,
            account_id=e.account_id,
            account_name=e.account_name,
            opportunity_id=e.opportunity_id,
            opportunity_name=e.opportunity_name,
            partner_id=e.partner_id,
            partner_name=e.partner_name,
            owner_name=e.owner_name,
            progress=e.progress,
            risks=e.risks,
            comments=_to_comments(e.comments) if include_comments else [],
        )
        for e in entities
    ]

    return WeeklyFollowupDetailOut(
        scope=scope,
        week_start=week_start,
        week_end=week_end,
        summary=summary_out,
        entities=WeeklyFollowupEntityPageOut(total=int(total or 0), page=page, size=size, items=items),
    )


@router.post("/crm/weekly-followup/detail/filter-options")
def get_weekly_followup_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: WeeklyFollowupFilterOptionsQueryIn,
) -> WeeklyFollowupFilterOptionsOut:
    """
    获取周总结详情页的筛选选项（部门名称、负责人名称）
    用于前端下拉选择框填充
    """
    can_view_team, is_company_admin, user_dept_id, user_dept_name = _can_view_weekly_followup(db_session, user)

    scope = payload.scope
    if scope == "company" and not is_company_admin:
        raise HTTPException(status_code=403, detail="权限不足：仅公司管理员可查看 company scope")

    week_start = payload.start_date
    week_end = payload.end_date
    is_sales_limited = bool(scope == "department" and (not is_company_admin) and (not can_view_team))

    # 解析部门过滤（仅 department scope）
    dept_id = None
    dept_name = None
    if scope == "department":
        if is_company_admin:
            dept_id = (payload.department_id or "").strip() or None
            dept_name = (payload.department_name or "").strip() or None
            if dept_id is None and dept_name is None:
                raise HTTPException(status_code=400, detail="department scope 需要指定 department_id 或 department_name")
        else:
            dept_id = user_dept_id
            dept_name = user_dept_name
            if dept_id is None and dept_name is None:
                raise HTTPException(status_code=403, detail="无法获取本团队信息")

    # 构建基础查询条件（与详情接口保持一致）
    conds = [
        CRMWeeklyFollowupEntitySummary.week_start == week_start,
        CRMWeeklyFollowupEntitySummary.week_end == week_end,
    ]
    if scope == "my":
        conds.append(CRMWeeklyFollowupEntitySummary.owner_user_id == str(user.id))
    elif scope == "department":
        if dept_id:
            conds.append(CRMWeeklyFollowupEntitySummary.department_id == dept_id)
        elif dept_name:
            conds.append(CRMWeeklyFollowupEntitySummary.department_name == dept_name)
        if is_sales_limited:
            # 普通销售：只能看自己负责的商机/客户明细
            conds.append(CRMWeeklyFollowupEntitySummary.owner_user_id == str(user.id))

    # 获取去重后的部门名称列表
    department_names = db_session.exec(
        select(distinct(CRMWeeklyFollowupEntitySummary.department_name))
        .where(*conds)
        .where(CRMWeeklyFollowupEntitySummary.department_name.is_not(None))
        .order_by(CRMWeeklyFollowupEntitySummary.department_name)
    ).all()

    # 获取去重后的负责人名称列表
    owner_names = db_session.exec(
        select(distinct(CRMWeeklyFollowupEntitySummary.owner_name))
        .where(*conds)
        .where(CRMWeeklyFollowupEntitySummary.owner_name.is_not(None))
        .order_by(CRMWeeklyFollowupEntitySummary.owner_name)
    ).all()

    return WeeklyFollowupFilterOptionsOut(
        department_names=[name for name in department_names if name],  # 过滤空字符串
        owner_names=[name for name in owner_names if name],  # 过滤空字符串
    )


@router.post("/crm/weekly-followup/trigger")
def trigger_weekly_followup_summary_task(
    # db_session: SessionDep,
    # user: CurrentUserDep,
    payload: WeeklyFollowupTriggerTaskIn = Body(default=WeeklyFollowupTriggerTaskIn()),
) -> WeeklyFollowupTriggerTaskOut:
    """
    人工触发“周跟进总结”生成任务（异步，返回 task_id）。
    - 暂时不做权限校验，方便测试
    - start_date/end_date 可不传；不传时任务内部按默认口径计算（上周日-本周六，北京时间）
    """
    # _, is_company_admin, _, _ = _can_view_weekly_followup(db_session, user)
    # if not is_company_admin:
    #     raise HTTPException(status_code=403, detail="权限不足：仅公司管理员可触发生成任务")

    start_date = payload.start_date
    end_date = payload.end_date
    if (start_date is None) != (end_date is None):
        raise HTTPException(status_code=400, detail="start_date/end_date 需要同时传或同时不传")

    # 延迟导入，避免路由模块加载时引入 Celery task 依赖
    from app.tasks.cron_jobs import generate_crm_weekly_followup_summary

    task = generate_crm_weekly_followup_summary.delay(
        start_date_str=start_date.isoformat() if start_date else None,
        end_date_str=end_date.isoformat() if end_date else None,
    )
    return WeeklyFollowupTriggerTaskOut(task_id=task.id, start_date=start_date, end_date=end_date, status="PENDING")


@router.post("/crm/weekly-followup/leader-engagement/trigger")
def trigger_weekly_followup_leader_engagement_report_task(
    payload: WeeklyFollowupTriggerTaskIn = Body(default=WeeklyFollowupTriggerTaskIn()),
) -> WeeklyFollowupTriggerTaskOut:
    """
    人工触发“周跟进总结 leader 阅读/互动统计推送”任务（异步，返回 task_id）。
    - 暂时不做权限校验，方便测试
    - start_date/end_date 可不传；不传时任务内部按默认口径计算（上周日-本周六，北京时间）
    """
    start_date = payload.start_date
    end_date = payload.end_date
    if (start_date is None) != (end_date is None):
        raise HTTPException(status_code=400, detail="start_date/end_date 需要同时传或同时不传")

    # 延迟导入，避免路由模块加载时引入 Celery task 依赖
    from app.tasks.cron_jobs import send_crm_weekly_followup_leader_engagement_report

    task = send_crm_weekly_followup_leader_engagement_report.delay(
        week_start_str=start_date.isoformat() if start_date else None,
        week_end_str=end_date.isoformat() if end_date else None,
    )
    return WeeklyFollowupTriggerTaskOut(task_id=task.id, start_date=start_date, end_date=end_date, status="PENDING")


@router.post("/crm/weekly-followup/query")
def list_weekly_followup_weekly_summaries(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: WeeklyFollowupWeeklyListQueryIn = Body(default=WeeklyFollowupWeeklyListQueryIn()),
) -> WeeklyFollowupWeeklyListOut:
    """
    每周跟进总结列表（每周一行）。
    不同用户 scope 不同：
    - department: 团队负责人/普通销售均可（返回团队周总结列表）
    - company: 公司管理员（返回公司周总结列表）
    """
    can_view_team, is_company_admin, user_dept_id, user_dept_name = _can_view_weekly_followup(db_session, user)

    scope = payload.scope
    # 列表层只展示 company/department 的“周总结行”
    if scope == "company" and not is_company_admin:
        raise HTTPException(status_code=403, detail="权限不足：仅公司管理员可查看 company scope")

    page = max(int(payload.page or 1), 1)
    size = max(min(int(payload.page_size or 20), 200), 1)
    offset = (page - 1) * size

    dept_id = None
    dept_name = None
    if scope == "department":
        if is_company_admin:
            dept_id = (payload.department_id or "").strip() or None
            dept_name = (payload.department_name or "").strip() or None
        else:
            # 非公司管理员：强制本部门（团队负责人/普通销售都一样）
            dept_id = user_dept_id
            dept_name = user_dept_name
        if (not is_company_admin) and (dept_id is None and dept_name is None):
            raise HTTPException(status_code=403, detail="无法获取本团队信息")

    items: List[WeeklyFollowupWeeklyListItemOut] = []

    if scope in {"company", "department"}:
        conds = [
            CRMWeeklyFollowupSummary.summary_type == ("company" if scope == "company" else "department"),
        ]
        if scope == "company":
            conds.append(CRMWeeklyFollowupSummary.department_name == "")
        else:
            if dept_id:
                conds.append(CRMWeeklyFollowupSummary.department_id == dept_id)
            if dept_name:
                conds.append(CRMWeeklyFollowupSummary.department_name == dept_name)

        # 按照周起始日期进行可选的起止日期过滤
        if payload.start_date:
            conds.append(CRMWeeklyFollowupSummary.week_start >= payload.start_date)
        if payload.end_date:
            conds.append(CRMWeeklyFollowupSummary.week_end <= payload.end_date)

        total = db_session.exec(select(func.count()).select_from(CRMWeeklyFollowupSummary).where(*conds)).one()
        rows = db_session.exec(
            select(CRMWeeklyFollowupSummary)
            .where(*conds)
            .order_by(CRMWeeklyFollowupSummary.week_start.desc(), CRMWeeklyFollowupSummary.updated_at.desc())
            .offset(offset)
            .limit(size)
        ).all()
        for s in rows:
            items.append(
                WeeklyFollowupWeeklyListItemOut(
                    summary_id=s.id,
                    scope=scope,
                    week_start=s.week_start,
                    week_end=s.week_end,
                    department_id=s.department_id or "",
                    department_name=s.department_name or "",
                    title=s.title or "",
                )
            )
        return WeeklyFollowupWeeklyListOut(total=int(total or 0), page=page, size=size, items=items)
    raise HTTPException(status_code=400, detail="scope must be 'department' or 'company'")


@router.get("/crm/weekly-followup/summaries/{summary_id}/reviewed")
def get_weekly_followup_summary_reviewed_status(
    db_session: SessionDep,
    user: CurrentUserDep,
    summary_id: UUID,
) -> WeeklyFollowupReviewStatusOut:
    """
    查询当前用户对部门周跟进总结的已阅状态，供前端控制按钮 enable/disable。
    """
    can_review, is_company_admin, user_dept_id, user_dept_name = _can_edit_weekly_followup_comments(db_session, user)

    summary = db_session.exec(select(CRMWeeklyFollowupSummary).where(CRMWeeklyFollowupSummary.id == summary_id)).first()
    if summary is None:
        raise HTTPException(status_code=404, detail="未找到相关周总结")
    if (summary.summary_type or "").strip() != "department":
        raise HTTPException(status_code=400, detail="仅支持团队/部门级周总结的已阅状态查询")

    # leader 只能确认本部门；公司管理员可确认任意部门
    can_review_current_summary = bool(can_review)
    if can_review_current_summary and not is_company_admin:
        if user_dept_id and (summary.department_id or "") and summary.department_id != user_dept_id:
            can_review_current_summary = False
        if (not user_dept_id) and user_dept_name and (summary.department_name or "") != user_dept_name:
            can_review_current_summary = False

    leader_user_id = str(getattr(user, "id", "") or "")
    eng = db_session.exec(
        select(CRMWeeklyFollowupLeaderEngagement).where(
            CRMWeeklyFollowupLeaderEngagement.summary_id == summary.id,
            CRMWeeklyFollowupLeaderEngagement.leader_user_id == leader_user_id,
        )
    ).first()
    reviewed_at = eng.reviewed_at if eng is not None else None

    return WeeklyFollowupReviewStatusOut(
        summary_id=summary.id,
        leader_user_id=leader_user_id,
        can_review=can_review_current_summary,
        reviewed=bool(reviewed_at),
        reviewed_at=reviewed_at,
    )


@router.post("/crm/weekly-followup/summaries/{summary_id}/reviewed")
def mark_weekly_followup_summary_reviewed(
    db_session: SessionDep,
    user: CurrentUserDep,
    summary_id: UUID,
) -> WeeklyFollowupLeaderEngagementOut:
    """
    团队负责人/公司管理员点击“已阅”，确认该部门周跟进总结。
    """
    can_review, is_company_admin, user_dept_id, user_dept_name = _can_edit_weekly_followup_comments(db_session, user)
    if not can_review:
        raise HTTPException(status_code=403, detail="权限不足：仅团队负责人或管理者可已阅确认")

    summary = db_session.exec(select(CRMWeeklyFollowupSummary).where(CRMWeeklyFollowupSummary.id == summary_id)).first()
    if summary is None:
        raise HTTPException(status_code=404, detail="未找到相关周总结")
    if (summary.summary_type or "").strip() != "department":
        raise HTTPException(status_code=400, detail="仅支持团队/部门级周总结的已阅确认")

    # leader 只能确认本部门；公司管理员可确认任意部门
    if not is_company_admin:
        if user_dept_id and (summary.department_id or "") and summary.department_id != user_dept_id:
            raise HTTPException(status_code=403, detail="权限不足：只能确认本团队周总结")
        if (not user_dept_id) and user_dept_name and (summary.department_name or "") != user_dept_name:
            raise HTTPException(status_code=403, detail="权限不足：只能确认本团队周总结")

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))
    leader_user_id = str(getattr(user, "id", "") or "")
    eng = crm_weekly_followup_engagement_service.upsert_engagement(
        db_session,
        summary=summary,
        leader_user_id=leader_user_id,
        reviewed_at=now_bj,
    )

    return WeeklyFollowupLeaderEngagementOut(
        summary_id=summary.id,
        leader_user_id=eng.leader_user_id,
        week_start=summary.week_start,
        week_end=summary.week_end,
        department_id=summary.department_id or "",
        department_name=summary.department_name or "",
        reviewed_at=eng.reviewed_at,
        commented_at=eng.commented_at,
    )


@router.post("/crm/weekly-followup/entities/{entity_id}/comments")
def save_weekly_followup_comments(
    db_session: SessionDep,
    user: CurrentUserDep,
    entity_id: UUID,
    payload: SaveWeeklyFollowupCommentsIn,
) -> WeeklyFollowupEntityRowOut:
    """
    3) 修改保存评论（整体覆盖保存）
    """
    # can_edit, is_company_admin, user_dept_id, user_dept_name = _can_edit_weekly_followup_comments(db_session, user)
    # if not can_edit:
    #     raise HTTPException(status_code=403, detail="权限不足：仅团队负责人或管理者可编辑评论")

    entity = db_session.exec(select(CRMWeeklyFollowupEntitySummary).where(CRMWeeklyFollowupEntitySummary.id == entity_id)).first()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity summary not found")

    # if not is_company_admin:
    #     if user_dept_id and getattr(entity, "department_id", None) and entity.department_id != user_dept_id:
    #         raise HTTPException(status_code=403, detail="权限不足：只能编辑本团队记录")
    #     if not user_dept_id and user_dept_name and entity.department_name != user_dept_name:
    #         raise HTTPException(status_code=403, detail="权限不足：只能编辑本团队记录")

    # 安全保护：只能覆盖“自己写的评论”，不得覆盖/删除他人的评论
    current_user_id = str(getattr(user, "id", "") or "")
    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))

    existing_raw = entity.comments if isinstance(entity.comments, list) else []
    kept_others: list[dict] = []
    for item in existing_raw:
        if not isinstance(item, dict):
            continue
        if str(item.get("author_id") or "") != current_user_id:
            kept_others.append(item)

    # 仅采纳 payload 中 author_id=当前用户 的评论；created_at 为空则用北京时间补齐
    my_comments: list[dict] = []
    for c in (payload.comments or []):
        if (c.author_id or "") != current_user_id:
            continue
        created_at = c.created_at or now_bj
        my_comments.append(
            {
                "author_id": current_user_id,
                "author": c.author or "",
                "content": c.content,
                "type": c.type or "comment",
                "created_at": created_at.isoformat(),
            }
        )

    # 合并回写（保持大体时间顺序；时间解析失败则放末尾）
    merged = kept_others + my_comments

    def _sort_key(x: dict) -> tuple[int, str]:
        v = str(x.get("created_at") or "")
        try:
            return (0, datetime.fromisoformat(v).isoformat())
        except Exception:
            return (1, v)

    merged.sort(key=_sort_key)
    entity.comments = merged
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)

    # leader 参与度：若当前用户是团队负责人，且本次确实提交了评论，则记录 commented_at
    try:
        if my_comments:
            # leader 判定与 _can_view_weekly_followup 保持一致（不依赖 OAuth 权限调用，避免引入额外延迟）
            user_profile_repo = UserProfileRepo()
            profile = user_profile_repo.get_by_user_id(db_session, user.id)
            is_leader_flag = user_department_relation_repo.get_is_leader_by_user_ids(
                db_session,
                [current_user_id],
            ).get(current_user_id)
            if is_leader_flag is None:
                is_team_lead = bool(profile and profile.department and not profile.direct_manager_id)
            else:
                is_team_lead = bool(is_leader_flag)

            if is_team_lead:
                summary = db_session.exec(
                    select(CRMWeeklyFollowupSummary).where(
                        CRMWeeklyFollowupSummary.week_start == entity.week_start,
                        CRMWeeklyFollowupSummary.week_end == entity.week_end,
                        CRMWeeklyFollowupSummary.summary_type == "department",
                        CRMWeeklyFollowupSummary.department_name == (entity.department_name or ""),
                    )
                ).first()
                if summary is not None:
                    crm_weekly_followup_engagement_service.upsert_engagement(
                        db_session,
                        summary=summary,
                        leader_user_id=current_user_id,
                        commented_at=now_bj,
                    )
    except Exception as e:
        logger.warning(f"记录周跟进 leader 评论参与度失败（不影响保存评论）：{e}")

    # 保存评论成功后：推送提醒给负责销售（不影响主流程，失败仅记录日志）
    try:
        owner_user_id = str(getattr(entity, "owner_user_id", "") or "")
        if owner_user_id and owner_user_id != current_user_id:
            # 如果最新一条评论是 task，则不做推送
            latest_comment_type = ""
            if my_comments:
                latest_comment_type = str((my_comments[-1] or {}).get("type") or "").strip().lower()
            if latest_comment_type != "task":
                from app.core.config import settings
                from urllib.parse import quote_plus

                # 选取本次写入的评论内容摘要（可能为空，允许）
                comment_preview = ""
                if my_comments:
                    comment_preview = str((my_comments[-1] or {}).get("content") or "").strip()
                if len(comment_preview) > 200:
                    comment_preview = comment_preview[:197] + "..."

                week_part = f"{entity.week_start.isoformat()}~{entity.week_end.isoformat()}"
                dept_name = (entity.department_name or "").strip()
                jump_url = (
                    f"{settings.REVIEW_REPORT_HOST}/review/opportunitySummary"
                    f"?department_name={quote_plus(dept_name)}"
                    f"&week_start={entity.week_start.isoformat()}&week_end={entity.week_end.isoformat()}"
                )

                author_name = my_comments[-1].get("author") if my_comments else ""
                author_name = str(author_name or "").strip() or "有人"

                text = (
                    f"{author_name}评论了你的周跟进总结（{week_part}）\n"
                    f"[{entity.account_name or entity.partner_name}  {entity.opportunity_name}]({jump_url})\n"
                    f"评论：{comment_preview or '--'}\n"
                )

                from app.services.platform_notification_service import platform_notification_service
                platform_notification_service.send_weekly_followup_comment_notification(
                    db_session,
                    recipient_user_id=owner_user_id,
                    message_text=text,
                )
    except Exception as e:
        logger.warning(f"发送周跟进评论提醒失败（不影响保存评论）：{e}")

    return WeeklyFollowupEntityRowOut(
        id=entity.id,
        department_name=entity.department_name,
        account_id=entity.account_id,
        account_name=entity.account_name,
        opportunity_id=entity.opportunity_id,
        opportunity_name=entity.opportunity_name,
        partner_id=entity.partner_id,
        partner_name=entity.partner_name,
        owner_name=entity.owner_name,
        progress=entity.progress,
        risks=entity.risks,
        comments=_to_comments(entity.comments),
    )
