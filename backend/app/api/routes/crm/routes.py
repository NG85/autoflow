import logging
import csv
import io
import hashlib
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from app.models.customer_document import CustomerDocument
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from app.crm.view_engine import CrmViewRequest, ViewType, CrmViewEngine, ViewRegistry
from fastapi_pagination import Page
from datetime import datetime, timedelta

from app.api.routes.crm.models import (
    Account,
    VisitRecordCreate,
    DailyReportRequest,
    DailyReportResponse,
    DailyReportStatistics,
    AssessmentDetail,
    DepartmentDailyReportResponse,
    CompanyDailyReportResponse,
    DepartmentWeeklyReportResponse,
    CompanyWeeklyReportResponse,
    WeeklyReportRequest,
    SalesTaskWeeklySummaryRequest,
    DepartmentSalesTaskWeeklySummaryResponse,
    CompanySalesTaskWeeklySummaryResponse,
    CustomerDocumentUploadRequest,
    WeeklyFollowupEntityRowOut,
    WeeklyFollowupDetailQueryIn,
    WeeklyFollowupDetailOut,
    WeeklyFollowupWeeklyListQueryIn,
    WeeklyFollowupWeeklyListOut,
    WeeklyFollowupWeeklyListItemOut,
    WeeklyFollowupTriggerTaskIn,
    WeeklyFollowupTriggerTaskOut,
    WeeklyFollowupSummaryItemOut,
    WeeklyFollowupDepartmentOption,
    WeeklyFollowupScope,
    WeeklyFollowupEntityPageOut,
    SaveWeeklyFollowupCommentsIn,
)
from app.crm.save_engine import (
    save_visit_record_to_crm_table, 
    push_visit_record_message,
    save_visit_record_with_content
)
from app.api.routes.crm.models import VisitRecordQueryRequest
from app.services.customer_document_service import CustomerDocumentService
from app.services.document_processing_service import document_processing_service
from app.services.crm_statistics_service import crm_statistics_service
from app.services.crm_sales_task_statistics_service import crm_sales_task_statistics_service
from app.models.crm_daily_account_statistics import CRMDailyAccountStatistics
from app.repositories.user_profile import UserProfileRepo
from app.repositories.visit_record import visit_record_repo
from app.repositories.document_content import DocumentContentRepo
from sqlmodel import select, or_, distinct, func
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_accounts import CRMAccount
from app.models.user_profile import UserProfile
from app.models.crm_weekly_followup_summary import CRMWeeklyFollowupSummary
from app.models.crm_weekly_followup_entity_summary import CRMWeeklyFollowupEntitySummary
from uuid import UUID
from pydantic import BaseModel
from app.repositories.user_profile import UserProfileRepo
from app.repositories.visit_record import visit_record_repo
from app.repositories.user_department_relation import user_department_relation_repo


logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize view registry
view_registry = ViewRegistry()

# Initialize view engine
view_engine = CrmViewEngine(view_registry=view_registry)

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

    roles_and_permissions = visit_record_repo._get_user_roles_and_permissions(user.id)
    permissions = roles_and_permissions.get("permissions", []) if isinstance(roles_and_permissions, dict) else []

    is_company_admin = "report51:company:view" in permissions or visit_record_repo._is_admin_user(user.id, db_session, permissions)
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
        if dept_id:
            conds.append(CRMWeeklyFollowupEntitySummary.department_id == dept_id)
        elif dept_name:
            conds.append(CRMWeeklyFollowupEntitySummary.department_name == dept_name)
        if is_sales_limited:
            # 普通销售：只能看自己负责的商机/客户明细
            conds.append(CRMWeeklyFollowupEntitySummary.owner_user_id == str(user.id))

    def _to_comments(v: object) -> list[WeeklyFollowupEntityRowOut.WeeklyFollowupComment]:
        if not include_comments:
            return []
        if not isinstance(v, list):
            return []
        out: list[WeeklyFollowupEntityRowOut.WeeklyFollowupComment] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            try:
                created_at_raw = item.get("created_at")
                created_at = None
                if created_at_raw:
                    created_at = datetime.fromisoformat(str(created_at_raw))
                out.append(
                    WeeklyFollowupEntityRowOut.WeeklyFollowupComment(
                        author_id=str(item.get("author_id") or ""),
                        author=str(item.get("author") or ""),
                        content=str(item.get("content") or ""),
                        created_at=created_at,
                    )
                )
            except Exception:
                continue
        return out

    total = db_session.exec(select(func.count()).select_from(CRMWeeklyFollowupEntitySummary).where(*conds)).one()
    entities = db_session.exec(
        select(CRMWeeklyFollowupEntitySummary)
        .where(*conds)
        .order_by(CRMWeeklyFollowupEntitySummary.department_name, CRMWeeklyFollowupEntitySummary.updated_at.desc())
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
            comments=_to_comments(e.comments),
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
    can_edit, is_company_admin, user_dept_id, user_dept_name = _can_edit_weekly_followup_comments(db_session, user)
    if not can_edit:
        raise HTTPException(status_code=403, detail="权限不足：仅团队负责人或管理者可编辑评论")

    entity = db_session.exec(select(CRMWeeklyFollowupEntitySummary).where(CRMWeeklyFollowupEntitySummary.id == entity_id)).first()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity summary not found")

    if not is_company_admin:
        if user_dept_id and getattr(entity, "department_id", None) and entity.department_id != user_dept_id:
            raise HTTPException(status_code=403, detail="权限不足：只能编辑本团队记录")
        if not user_dept_id and user_dept_name and entity.department_name != user_dept_name:
            raise HTTPException(status_code=403, detail="权限不足：只能编辑本团队记录")

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
                "author": (c.author or "") or (getattr(user, "email", "") or getattr(user, "name", "") or ""),
                "content": c.content,
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

    def _to_comments(v: object) -> list[WeeklyFollowupEntityRowOut.WeeklyFollowupComment]:
        if not isinstance(v, list):
            return []
        out: list[WeeklyFollowupEntityRowOut.WeeklyFollowupComment] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            try:
                created_at_raw = item.get("created_at")
                created_at = None
                if created_at_raw:
                    created_at = datetime.fromisoformat(str(created_at_raw))
                out.append(
                    WeeklyFollowupEntityRowOut.WeeklyFollowupComment(
                        author_id=str(item.get("author_id") or ""),
                        author=str(item.get("author") or ""),
                        content=str(item.get("content") or ""),
                        created_at=created_at,
                    )
                )
            except Exception:
                continue
        return out

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


@router.post("/crm/views", response_model=Page[Account])
def query_crm_view(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: CrmViewRequest,
):
    try:
        # 使用 execute_view_query 获取分页数据
        result = view_engine.execute_view_query(
            db_session=db_session,
            request=request,
            user_id=user.id
        )
        
        # 转换为 Page 格式
        return Page(
            items=result["data"],
            total=result["total"],
            page=result["page"],
            size=result["page_size"],
            pages=result["total_pages"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.get("/crm/views/fields")
async def get_view_fields(
    view_type: ViewType = ViewType.STANDARD
):
    try:
        fields = view_engine.view_registry.get_all_fields()
        return fields
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.get("/crm/views/filter-options")
async def get_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
):
    try:
        return view_engine.get_filter_options(
            db_session=db_session,
            user_id=user.id
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_record")
def create_visit_record(
    db_session: SessionDep,
    user: CurrentUserDep,
    record: VisitRecordCreate,
    force: bool = Body(False, example=False),
    feishu_auth_code: Optional[str] = Body(None, description="飞书授权码，用于换取访问令牌")
):
    """
    创建拜访记录
    支持简易版和完整版表单
    """
    try:
        if not record.visit_type:
            record.visit_type = "form"
        
        # 确保记录人ID与当前用户ID一致
        if record.recorder_id:
            try:
                recorder_id = UUID(record.recorder_id)
                if not user.is_superuser and recorder_id != user.id:
                    return {"code": 400, "message": "记录人ID必须与当前用户ID一致", "data": {}}
            except ValueError:
                return {"code": 400, "message": "记录人ID格式无效，应为有效的UUID", "data": {}}
        else:
            logger.info(f"Fill in recorder id with current user id: {user.id}")
            record.recorder_id = str(user.id)

        if not record.recorder or record.recorder == '未知用户':
            logger.info(f"Fill in recorder name with recorder id: {record.recorder_id}")
            user_profile = UserProfileRepo().get_by_recorder_id(db_session, record.recorder_id)
            logger.info(f"User profile: {user_profile}")
            if user_profile:
                record.recorder = user_profile.name
                logger.info(f"Filled in recorder name: {user_profile.name}")
            else:
                logger.warning(f"Could not find user profile for recorder_id: {record.recorder_id}")

        # 根据拜访类型处理
        if record.visit_type == "link":
            if not record.visit_url:
                return {"code": 400, "message": "visit_url is required", "data": {}}
            
            # 使用通用文档处理服务
            result = document_processing_service.process_document_url(
                document_url=record.visit_url,
                user_id=str(user.id),
                auth_code=feishu_auth_code
            )
            
            # 如果处理失败，直接返回结果
            if not result.get("success"):
                # 转换响应格式以匹配拜访记录的API格式
                if result.get("data", {}).get("auth_required"):
                    data = result["data"]
                    return {
                        "code": 401,
                        "message": result["message"],
                        "data": data
                    }
                else:
                    return {
                        "code": 400,
                        "message": result["message"],
                        "data": result.get("data", {})
                    }
            
            # 处理成功，保存拜访记录和文档内容
            try:
                result_data = save_visit_record_with_content(
                    record=record,
                    content=result["content"],
                    document_type=result["document_type"],
                    user=user,
                    db_session=db_session,
                    title=result.get("title")
                )
                
                # 提交事务
                db_session.commit()
                return result_data
            except Exception as e:
                # 如果保存失败，回滚事务
                db_session.rollback()
                logger.error(f"Failed to save visit record: {e}")
                return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
        
        # 处理 form 类型的拜访记录（包括 force 和普通保存）
        if force:
            # 直接保存，不做AI判断
            try:
                record_id, saved_time = save_visit_record_to_crm_table(record, db_session)
                db_session.commit()
                # 推送飞书消息（attachment 由下游统一做瘦身与解析）
                record_data = record.model_dump()
                push_visit_record_message(
                    visit_type=record.visit_type,
                    sales_visit_record=record_data,
                    db_session=db_session,
                    meeting_notes=None,
                    saved_time=saved_time
                )
                return {"code": 0, "message": "success", "data": {}}
            except Exception as e:
                db_session.rollback()
                logger.error(f"Failed to save visit record with force: {e}")
                return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
        
        # 根据表单类型处理数据
        from app.core.config import settings
        form_type = record.form_type or settings.CRM_VISIT_RECORD_FORM_TYPE.value

        # 使用可靠的处理函数，分组处理任务
        from app.crm.save_engine import process_visit_record_content_reliable
        
        # 根据表单类型调用可靠函数
        if form_type == "simple":
            # 简易版表单：传入followup_content
            result = process_visit_record_content_reliable(followup_content=record.followup_content)
        else:
            # 完整版表单：传入followup_record和next_steps
            result = process_visit_record_content_reliable(
                followup_record=record.followup_record,
                next_steps=record.next_steps
            )
        
        # 将处理结果赋值给record
        record.followup_record = result["followup_record"]
        record.followup_record_zh = result["followup_record_zh"]
        record.followup_record_en = result["followup_record_en"]
        record.followup_quality_level_zh = result["followup_quality_level_zh"]
        record.followup_quality_level_en = result["followup_quality_level_en"]
        record.followup_quality_reason_zh = result["followup_quality_reason_zh"]
        record.followup_quality_reason_en = result["followup_quality_reason_en"]
        record.next_steps = result["next_steps"]
        record.next_steps_zh = result["next_steps_zh"]
        record.next_steps_en = result["next_steps_en"]
        record.next_steps_quality_level_zh = result["next_steps_quality_level_zh"]
        record.next_steps_quality_level_en = result["next_steps_quality_level_en"]
        record.next_steps_quality_reason_zh = result["next_steps_quality_reason_zh"]
        record.next_steps_quality_reason_en = result["next_steps_quality_reason_en"]
        
        # 构建返回数据
        data = {
            "followup": {
                "level_zh": result["followup_quality_level_zh"], 
                "reason_zh": result["followup_quality_reason_zh"], 
                "content": result["followup_record"],
                "content_zh": result["followup_record_zh"],
                "content_en": result["followup_record_en"],
                "level_en": result["followup_quality_level_en"],
                "reason_en": result["followup_quality_reason_en"]
            },
            "next_steps": {
                "level_zh": result["next_steps_quality_level_zh"], 
                "reason_zh": result["next_steps_quality_reason_zh"], 
                "content": result["next_steps"],
                "content_zh": result["next_steps_zh"],
                "content_en": result["next_steps_en"],
                "level_en": result["next_steps_quality_level_en"],
                "reason_en": result["next_steps_quality_reason_en"]
            }
        }
        
        # 质量检查：只要有一项不合格就阻止保存
        if (result["followup_quality_level_zh"] == "不合格" or result["next_steps_quality_level_zh"] == "不合格" or 
            result["followup_quality_level_en"] == "unqualified" or result["next_steps_quality_level_en"] == "unqualified"):
            return {"code": 400, "message": "failed", "data": data}

        try:
            record_id, saved_time = save_visit_record_to_crm_table(record, db_session)
            db_session.commit()
            # 推送飞书消息（attachment 由下游统一做瘦身与解析）
            record_data = record.model_dump()
            push_visit_record_message(
                visit_type=record.visit_type,
                sales_visit_record=record_data,
                db_session=db_session,
                meeting_notes=None,
                saved_time=saved_time
            )
            return {"code": 0, "message": "success", "data": data}
        except Exception as e:
            db_session.rollback()
            logger.error(f"Failed to save visit record after quality check: {e}")
            return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
    


@router.post("/crm/visit_record/verify")
def verify_visit_record(
    user: CurrentUserDep,
    followup_content: Optional[str] = Body(None, example=""),
    followup_record: Optional[str] = Body(None, example=""),
    next_steps: Optional[str] = Body(None, example=""),
):
    try:
        from app.crm.save_engine import process_visit_record_content_reliable
        
        # 使用统一的处理流程
        if followup_content:
            result = process_visit_record_content_reliable(followup_content=followup_content)
        else:
            result = process_visit_record_content_reliable(
                followup_record=followup_record,
                next_steps=next_steps
            )
        
        data = {
            "followup": {
                "level_zh": result["followup_quality_level_zh"],
                "reason_zh": result["followup_quality_reason_zh"],
                "content": result["followup_record"],
                "content_zh": result["followup_record_zh"],
                "content_en": result["followup_record_en"],
                "level_en": result["followup_quality_level_en"],
                "reason_en": result["followup_quality_reason_en"]
            },
            "next_steps": {
                "level_zh": result["next_steps_quality_level_zh"],
                "reason_zh": result["next_steps_quality_reason_zh"],
                "content": result["next_steps"],
                "content_zh": result["next_steps_zh"],
                "content_en": result["next_steps_en"],
                "level_en": result["next_steps_quality_level_en"],
                "reason_en": result["next_steps_quality_reason_en"]
            }
        }
        
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_records/query")
def query_visit_records(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: VisitRecordQueryRequest,
):
    """
    查询CRM拜访记录
    支持条件查询和分页
    根据当前用户的汇报关系限制数据访问权限
    """
    try:
        
        result = visit_record_repo.query_visit_records(
            session=db_session,
            request=request,
            current_user_id=user.id
        )
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "items": [item.model_dump() for item in result.items],
                "total": result.total,
                "page": result.page,
                "page_size": result.size,
                "pages": result.pages
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_records/export")
def export_visit_records_to_csv(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: VisitRecordQueryRequest,
):
    """
    导出CRM拜访记录到CSV文件
    支持条件查询和分页
    根据当前用户的汇报关系限制数据访问权限
    支持中英文版本导出
    """
    try:
        # 创建CSV内容
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 根据语言参数确定CSV头部和数据内容
        language = request.language or "zh"  # 默认为中文
        
        if language == "en":
            # 英文版CSV头部 - 只包含英文字段
            headers = [
                "ID", "Customer Level", "Account Name", "Account ID", "First Visit", "Call High",
                "Partner Name", "Partner ID", "Opportunity Name", "Opportunity ID", "Follow-up Date", "Person in Charge", "Department",
                "Contact Position", "Contact Name", "Collaborative Participants", "Follow-up Method",
                "Visit Purpose", "Attachment Location", "Attachment Latitude", "Attachment Longitude", "Attachment Taken At", "Follow-up Record", 
                "AI Follow-up Record Quality Evaluation", "AI Follow-up Record Quality Evaluation Details", 
                "Next Steps", "AI Next Steps Quality Evaluation", "AI Next Steps Quality Evaluation Details",
                "Record Type", "Information Source", "Remarks", "Created Time"
            ]
        else:
            # 中文版CSV头部（默认）- 只包含中文字段
            headers = [
                "ID", "客户分类", "客户名称", "客户ID", "是否首次拜访", "是否Call High",
                "合作伙伴", "合作伙伴ID", "商机名称", "商机ID", "跟进日期", "负责销售", "所在团队",
                "客户岗位", "客户名字", "协同参与人", "跟进方式",
                "拜访目的", "附件地点", "附件纬度", "附件经度", "附件拍摄时间", "跟进记录", 
                "AI对跟进记录质量评估", "AI对跟进记录质量评估详情",
                "下一步计划", "AI对下一步计划质量评估", "AI对下一步计划质量评估详情",
                "记录类型", "信息来源", "备注", "创建时间"
            ]
        
        writer.writerow(headers)
        
        # 使用分页查询循环获取所有数据
        # 限制最大导出10000条记录
        # 如果用户指定了page_size且大于0，则使用用户指定的值（但不超过10000）
        # 否则默认导出最多10000条
        if request.page_size and request.page_size > 0:
            max_export_count = min(request.page_size, 10000)
        else:
            max_export_count = 10000
        page_size = 100  # 每次查询100条（fastapi_pagination的限制）
        current_page = 1
        total_exported = 0
        total_pages = 0
        
        # 辅助函数：将单个item转换为CSV行
        def item_to_csv_row(item):
            # 根据语言选择对应的字段值
            is_en = language == "en"
            
            # 生成基于关键字段的hash ID
            # 使用客户名称、跟进日期、负责销售等关键字段生成唯一ID
            key_fields = [
                str(item.id or ""),
                str(item.account_name or item.partner_name or item.opportunity_name or ""),
                str(item.visit_communication_date or ""),
                str(item.recorder or ""),
                str(item.contact_name or ""),
                str(item.last_modified_time or ""),
            ]
            key_string = "|".join(key_fields)
            record_id = hashlib.md5(key_string.encode('utf-8')).hexdigest()[:12]  # 取前12位作为ID
            
            # 布尔值字段的本地化处理
            first_visit_text = "Yes" if item.is_first_visit else "No" if item.is_first_visit is not None else ""
            call_high_text = "Yes" if item.is_call_high else "No" if item.is_call_high is not None else ""
            if not is_en:
                first_visit_text = "是" if item.is_first_visit else "否" if item.is_first_visit is not None else ""
                call_high_text = "是" if item.is_call_high else "否" if item.is_call_high is not None else ""
            
            # 多语言字段的本地化处理
            followup_record = item.followup_record_en if is_en else item.followup_record_zh
            followup_record = followup_record or item.followup_record or ""
            
            followup_quality_level = item.followup_quality_level_en if is_en else item.followup_quality_level_zh or ""
            followup_quality_reason = item.followup_quality_reason_en if is_en else item.followup_quality_reason_zh or ""
            
            next_steps = item.next_steps_en if is_en else item.next_steps_zh
            next_steps = next_steps or item.next_steps or ""
            
            next_steps_quality_level = item.next_steps_quality_level_en if is_en else item.next_steps_quality_level_zh or ""
            next_steps_quality_reason = item.next_steps_quality_reason_en if is_en else item.next_steps_quality_reason_zh or ""
            
            # 处理记录类型字段的多语言显示
            from app.api.routes.crm.models import RecordType
            record_type = ""
            if item.record_type:
                record_type_enum = RecordType.from_english(item.record_type)
                if record_type_enum:
                    record_type = record_type_enum.english if is_en else record_type_enum.chinese
                else:
                    record_type = item.record_type
            
            # 从附件中解析位置信息和经纬度
            attachment = getattr(item, "attachment", None)
            if attachment:
                # 结构化附件（VisitAttachment）
                location = getattr(attachment, "location", None) or ""
                latitude = getattr(attachment, "latitude", None) or ""
                longitude = getattr(attachment, "longitude", None) or ""
                taken_at = getattr(attachment, "taken_at", None) or ""
            else:
                location = ""
                latitude = ""
                longitude = ""
                taken_at = ""
            # 构建数据行（中英版本字段顺序相同，ID列在最前面）
            return [
                item.record_id or record_id,
                item.customer_level or "",
                item.account_name or "",
                item.account_id or "",
                first_visit_text,
                call_high_text,
                item.partner_name or "",
                item.partner_id or "",
                item.opportunity_name or "",
                item.opportunity_id or "",
                item.visit_communication_date or "",
                item.recorder or "",
                item.department or "",
                item.contact_position or "",
                item.contact_name or "",
                item.collaborative_participants or "",
                item.visit_communication_method or "",
                item.visit_purpose or "",
                location,
                latitude,
                longitude,
                taken_at,
                followup_record,
                followup_quality_level,
                followup_quality_reason,
                next_steps,
                next_steps_quality_level,
                next_steps_quality_reason,
                record_type,
                item.visit_type or "",
                item.remarks or "",
                item.last_modified_time or ""
            ]
        
        # 循环分页查询并写入数据
        while total_exported < max_export_count:
            # 查询当前页
            export_request = request.model_copy()
            export_request.page = current_page
            export_request.page_size = page_size
            
            result = visit_record_repo.query_visit_records(
                session=db_session,
                request=export_request,
                current_user_id=user.id
            )
            
            # 第一次查询时，获取总数和总页数
            if current_page == 1:
                # 计算需要查询的总页数（不超过最大导出数量）
                total_pages = min((max_export_count + page_size - 1) // page_size, result.pages)
            
            # 如果没有数据，退出循环
            if not result.items:
                break
            
            # 写入当前页的数据
            for item in result.items:
                if total_exported >= max_export_count:
                    break
                writer.writerow(item_to_csv_row(item))
                total_exported += 1
            
            # 如果当前页数据不足一页，说明已经是最后一页
            if len(result.items) < page_size:
                break
            
            # 如果已经达到需要查询的总页数，退出循环
            if current_page >= total_pages:
                break
            
            current_page += 1
        
        # 准备文件下载
        output.seek(0)
        csv_content = output.getvalue()
        output.close()
        
        # 生成文件名（包含语言标识）
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        language_suffix = "_en" if language == "en" else "_zh"
        filename = f"visit_records_export{language_suffix}_{current_time}.csv"
        
        # 创建响应
        def iter_csv():
            yield csv_content.encode('utf-8-sig')  # 使用utf-8-sig编码以支持Excel正确显示中文
        
        return StreamingResponse(
            iter_csv(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8-sig"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/visit_records/filter-options")
def get_visit_record_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
    form_type: Optional[Literal["simple", "complete"]] = None,
):
    """
    获取拜访记录查询的过滤选项
    用于前端下拉选择框等
    根据表单类型配置返回相应的字段
    """
    try:
        if not form_type:
            from app.core.config import settings
            form_type = settings.CRM_VISIT_RECORD_FORM_TYPE.value
        
        # 通用字段：无论哪种类型都返回
        # 获取客户名称选项
        account_names = db_session.exec(
            select(distinct(CRMSalesVisitRecord.account_name))
            .where(CRMSalesVisitRecord.account_name.is_not(None))
            .order_by(CRMSalesVisitRecord.account_name)
        ).all()
        
        # 获取合作伙伴选项
        partner_names = db_session.exec(
            select(distinct(CRMSalesVisitRecord.partner_name))
            .where(CRMSalesVisitRecord.partner_name.is_not(None))
            .order_by(CRMSalesVisitRecord.partner_name)
        ).all()
        
        # 获取记录人选项
        recorders = db_session.exec(
            select(distinct(CRMSalesVisitRecord.recorder))
            .where(CRMSalesVisitRecord.recorder.is_not(None))
            .order_by(CRMSalesVisitRecord.recorder)
        ).all()
        
        # 获取跟进质量等级选项（中英文）
        followup_quality_levels_zh = db_session.exec(
            select(distinct(CRMSalesVisitRecord.followup_quality_level_zh))
            .where(CRMSalesVisitRecord.followup_quality_level_zh.is_not(None))
            .order_by(CRMSalesVisitRecord.followup_quality_level_zh)
        ).all()
        
        followup_quality_levels_en = db_session.exec(
            select(distinct(CRMSalesVisitRecord.followup_quality_level_en))
            .where(CRMSalesVisitRecord.followup_quality_level_en.is_not(None))
            .order_by(CRMSalesVisitRecord.followup_quality_level_en)
        ).all()
        
        # 获取下一步计划质量等级选项（中英文）
        next_steps_quality_levels_zh = db_session.exec(
            select(distinct(CRMSalesVisitRecord.next_steps_quality_level_zh))
            .where(CRMSalesVisitRecord.next_steps_quality_level_zh.is_not(None))
            .order_by(CRMSalesVisitRecord.next_steps_quality_level_zh)
        ).all()
        
        next_steps_quality_levels_en = db_session.exec(
            select(distinct(CRMSalesVisitRecord.next_steps_quality_level_en))
            .where(CRMSalesVisitRecord.next_steps_quality_level_en.is_not(None))
            .order_by(CRMSalesVisitRecord.next_steps_quality_level_en)
        ).all()
        
        # 获取客户分类选项
        customer_levels = db_session.exec(
            select(distinct(CRMAccount.customer_level))
            .where(CRMAccount.customer_level.is_not(None))
            .order_by(CRMAccount.customer_level)
        ).all()
        
        # 获取部门选项 - 从用户档案表获取拜访人的部门
        departments = db_session.exec(
            select(distinct(UserProfile.department))
            .where(UserProfile.department.is_not(None))
            .order_by(UserProfile.department)
        ).all()
        
        # 基础返回数据
        result_data = {
            "account_names": account_names,
            "partner_names": partner_names,
            "recorders": recorders,
            "followup_quality_levels_zh": followup_quality_levels_zh,
            "followup_quality_levels_en": followup_quality_levels_en,
            "next_steps_quality_levels_zh": next_steps_quality_levels_zh,
            "next_steps_quality_levels_en": next_steps_quality_levels_en,
            "customer_levels": customer_levels,
            "departments": departments,
        }
        
        # 根据表单类型添加特定字段
        if form_type == "simple":
            # 简易版：添加拜访主题
            subjects = db_session.exec(
                select(distinct(CRMSalesVisitRecord.subject))
                .where(CRMSalesVisitRecord.subject.is_not(None))
                .order_by(CRMSalesVisitRecord.subject)
            ).all()
            result_data["subjects"] = subjects
        else:
            # 完整版：添加其他现有字段
            communication_methods = db_session.exec(
                select(distinct(CRMSalesVisitRecord.visit_communication_method))
                .where(CRMSalesVisitRecord.visit_communication_method.is_not(None))
                .order_by(CRMSalesVisitRecord.visit_communication_method)
            ).all()
            
            visit_purposes = db_session.exec(
                select(distinct(CRMSalesVisitRecord.visit_purpose))
                .where(CRMSalesVisitRecord.visit_purpose.is_not(None))
                .order_by(CRMSalesVisitRecord.visit_purpose)
            ).all()
            
            visit_types = db_session.exec(
                select(distinct(CRMSalesVisitRecord.visit_type))
                .where(CRMSalesVisitRecord.visit_type.is_not(None))
                .order_by(CRMSalesVisitRecord.visit_type)
            ).all()
            
            result_data.update({
                "communication_methods": communication_methods,
                "visit_purposes": visit_purposes,
                "visit_types": visit_types,
            })
        
        return {
            "code": 0,
            "message": "success",
            "data": result_data
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/visit_records/{record_id}")
def get_visit_record_by_id(
    db_session: SessionDep,
    user: CurrentUserDep,
    record_id: int | str,
):
    """
    根据ID获取单个拜访记录详情
    根据当前用户的汇报关系限制数据访问权限
    """
    try:
        record = visit_record_repo.get_visit_record_by_id(
            session=db_session,
            record_id=record_id,
            current_user_id=user.id
        )
        
        if not record:
            raise HTTPException(status_code=404, detail="拜访记录不存在或无权限访问")
        
        # 基础数据
        data = record.model_dump()

        # 如果是 link 类型的拜访记录，尝试返回从文档中抽取的问答对
        try:
            if getattr(record, "visit_type", None) == "link":
                document_content_repo = DocumentContentRepo()
                # visit_record_id 在 DocumentContent 中对应的是 CRM 表里的 record_id 字段
                visit_record_id = getattr(record, "record_id", None)
                if visit_record_id:
                    document_content = document_content_repo.get_by_visit_record_id(
                        session=db_session,
                        visit_record_id=visit_record_id,
                    )
                    if document_content:
                        data["document_qa_pairs"] = document_content.qa_pairs or []
                        data["document_qa_extract_status"] = document_content.qa_extract_status or ""
        except Exception as e:
            # 问答对加载失败不影响主流程，只记录日志
            logger.warning(f"加载文档问答对失败: record_id={record_id}, error={e}")
        
        return {
            "code": 0,
            "message": "success",
            "data": data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/daily-reports", response_model=Page[DailyReportResponse])
def get_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: DailyReportRequest,
):
    """
    获取销售个人日报信息
    
    返回销售人员的日报统计数据，包括：
    - 记录人/销售人员
    - 部门名称
    - 报告日期
    - 统计数据（客户跟进、合作伙伴跟进、评估等级统计）
    - 拜访记录详情页面链接
    
    支持的查询条件：
    - sales_id: 精确匹配销售人员ID
    - sales_name: 模糊匹配销售人员姓名
    - start_date/end_date: 日期范围过滤
    - department_name: 精确匹配部门名称
    - 分页参数: page, page_size
    """
    try:
        # 如果没有指定日期范围，使用最近7天的数据
        if not request.start_date and not request.end_date:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        else:
            start_date = request.start_date or (datetime.now().date() - timedelta(days=7))
            end_date = request.end_date or datetime.now().date()
        
        # 收集所有日报数据
        all_reports = []
        current_date = start_date
        
        while current_date <= end_date:
            daily_reports = crm_statistics_service.get_complete_daily_report(
                session=db_session, 
                target_date=current_date
            )
            
            for report in daily_reports:
                # 应用过滤条件
                if request.sales_id and report.get('sales_id') != request.sales_id:
                    continue
                if request.sales_name and request.sales_name.lower() not in (report.get('sales_name', '')).lower():
                    continue
                if request.department_name and report.get('department_name') != request.department_name:
                    continue
                
                # 转换为API响应格式
                daily_report = DailyReportResponse(
                    recorder=report['sales_name'],
                    department_name=report['department_name'],
                    report_date=report['report_date'],
                    statistics=[DailyReportStatistics(
                        end_customer_total_follow_up=report.get('end_customer_total_follow_up', 0),
                        end_customer_total_first_visit=report.get('end_customer_total_first_visit', 0),
                        end_customer_total_multi_visit=report.get('end_customer_total_multi_visit', 0),
                        partner_total_follow_up=report.get('partner_total_follow_up', 0),
                        partner_total_first_visit=report.get('partner_total_first_visit', 0),
                        partner_total_multi_visit=report.get('partner_total_multi_visit', 0),
                        assessment_red_count=report.get('assessment_red_count', 0),
                        assessment_yellow_count=report.get('assessment_yellow_count', 0),
                        assessment_green_count=report.get('assessment_green_count', 0)
                    )],
                    visit_detail_page=report['visit_detail_page'],
                    account_list_page=report['account_list_page'],
                    first_assessment=[AssessmentDetail(**assessment) for assessment in report['first_assessment']],
                    multi_assessment=[AssessmentDetail(**assessment) for assessment in report['multi_assessment']]
                )
                all_reports.append(daily_report)
            
            current_date += timedelta(days=1)
        
        # 按日期降序排序
        all_reports.sort(key=lambda x: x.report_date, reverse=True)
        
        # 手动分页
        total = len(all_reports)
        start_idx = (request.page - 1) * request.page_size
        end_idx = start_idx + request.page_size
        paginated_reports = all_reports[start_idx:end_idx]
        
        total_pages = (total + request.page_size - 1) // request.page_size
        
        return Page(
            items=paginated_reports,
            total=total,
            page=request.page,
            size=request.page_size,
            pages=total_pages
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/daily-reports/filter-options")
def get_daily_report_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    获取销售个人日报的过滤选项
    用于前端下拉选择框等
    """
    try:
        # 获取销售人员选项
        sales_names = db_session.exec(
            select(distinct(CRMDailyAccountStatistics.sales_name))
            .where(CRMDailyAccountStatistics.sales_name.is_not(None))
            .order_by(CRMDailyAccountStatistics.sales_name)
        ).all()
        
        # 获取部门选项
        department_names = db_session.exec(
            select(distinct(CRMDailyAccountStatistics.department_name))
            .where(CRMDailyAccountStatistics.department_name.is_not(None))
            .order_by(CRMDailyAccountStatistics.department_name)
        ).all()
        
        # 获取日期范围
        date_range = db_session.exec(
            select(
                func.min(CRMDailyAccountStatistics.report_date),
                func.max(CRMDailyAccountStatistics.report_date)
            )
        ).first()
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "sales_names": sales_names,
                "department_names": department_names,
                "date_range": {
                    "min_date": date_range[0].isoformat() if date_range[0] else None,
                    "max_date": date_range[1].isoformat() if date_range[1] else None,
                }
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.post("/crm/department-daily-reports", response_model=List[DepartmentDailyReportResponse])
def get_department_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天")
):
    """
    获取部门日报数据
    
    按部门汇总销售人员的日报数据：
    1. 统计数据直接加和
    2. 评估数据直接合并
    3. 包含各销售的详细日报信息
    
    Args:
        target_date: 目标日期，格式YYYY-MM-DD
        
    Returns:
        部门日报数据列表
    """
    try:
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD格式")
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 查询 {parsed_date} 的部门日报数据")
        
        # 获取部门汇总报告
        department_reports = crm_statistics_service.aggregate_department_reports(
            session=db_session,
            target_date=parsed_date
        )
        
        if not department_reports:
            logger.warning(f"{parsed_date} 没有找到任何部门日报数据")
            return []
        
        # 转换为响应格式
        response_reports = []
        for report in department_reports:
            # 转换日期格式
            if hasattr(report.get('report_date'), 'isoformat'):
                report['report_date'] = report['report_date'].isoformat()
            
            # 构造响应对象
            department_response = DepartmentDailyReportResponse(**report)
            response_reports.append(department_response)
        
        logger.info(f"成功返回 {len(response_reports)} 个部门的日报数据")
        return response_reports
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取部门日报数据失败: {e}")
        raise InternalServerError()


@router.post("/crm/company-daily-report", response_model=CompanyDailyReportResponse)
def get_company_daily_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天")
):
    """
    获取公司日报数据
    
    汇总全公司销售人员的日报数据：
    1. 统计数据直接加和
    2. 评估数据直接合并（但不包含跟进记录字段，且过滤绿灯评估）
    3. 提供公司级的整体数据概览
    
    注意：推送逻辑根据应用环境区分
    - 内部环境：推送到匹配的群聊
    - 外部环境：推送给外部管理员
    
    Args:
        target_date: 目标日期，格式YYYY-MM-DD
        
    Returns:
        公司日报数据
    """
    try:
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD格式")
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 查询 {parsed_date} 的公司日报数据")
        
        # 获取公司汇总报告
        company_report = crm_statistics_service.aggregate_company_report(
            session=db_session,
            target_date=parsed_date
        )
        
        if not company_report:
            logger.warning(f"{parsed_date} 没有找到任何公司日报数据")
            raise HTTPException(status_code=400, detail=f"{parsed_date} 没有找到任何数据")
        
        # 转换日期格式
        if hasattr(company_report.get('report_date'), 'isoformat'):
            company_report['report_date'] = company_report['report_date'].isoformat()
        
        # 构造响应对象
        company_response = CompanyDailyReportResponse(**company_report)
        
        logger.info(f"成功返回公司日报数据")
        return company_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取公司日报数据失败: {e}")
        raise InternalServerError()


@router.post("/crm/department-weekly-reports", response_model=List[DepartmentWeeklyReportResponse])
def get_department_weekly_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取团队周报数据
    
    按部门汇总销售人员的周报数据：
    1. 统计数据直接加和
    2. 计算平均值（总跟进数除以销售人员数量）
    3. 提供周报相关的页面链接
    
    Args:
        request: 周报查询请求，包含部门名称和日期范围
        
    Returns:
        团队周报数据列表
    """
    try:
        # 解析日期范围
        if request.start_date and request.end_date:
            start_date = request.start_date
            end_date = request.end_date
        else:
            # 默认查询最近一周的数据
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        
        logger.info(f"用户 {user.id} 查询 {start_date} 到 {end_date} 的团队周报数据")
        
        # 获取部门汇总报告
        department_reports = crm_statistics_service.aggregate_department_weekly_reports(
            session=db_session,
            start_date=start_date,
            end_date=end_date
        )
        
        if not department_reports:
            logger.warning(f"{start_date} 到 {end_date} 没有找到任何团队周报数据")
            return []
        
        # 如果指定了部门名称，进行过滤
        if request.department_name:
            department_reports = [
                report for report in department_reports 
                if report.get('department_name') == request.department_name
            ]
        
        # 转换为响应格式
        response_reports = []
        for report in department_reports:
            # 转换日期格式
            if hasattr(report.get('report_start_date'), 'isoformat'):
                report['report_start_date'] = report['report_start_date'].isoformat()
            if hasattr(report.get('report_end_date'), 'isoformat'):
                report['report_end_date'] = report['report_end_date'].isoformat()
            
            # 构造响应对象
            department_response = DepartmentWeeklyReportResponse(**report)
            response_reports.append(department_response)
        
        logger.info(f"成功返回 {len(response_reports)} 个团队的周报数据")
        return response_reports
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取团队周报数据失败: {e}")
        raise InternalServerError()


@router.post("/crm/company-weekly-report", response_model=CompanyWeeklyReportResponse)
def get_company_weekly_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取公司周报数据
    
    汇总所有部门的周报数据：
    1. 统计数据直接加和
    2. 计算平均值（总跟进数除以公司所有销售人员数量）
    3. 提供周报相关的页面链接
    
    Args:
        request: 周报查询请求，包含日期范围
        
    Returns:
        公司周报数据
    """
    try:
        # 解析日期范围
        if request.start_date and request.end_date:
            start_date = request.start_date
            end_date = request.end_date
        else:
            # 默认查询最近一周的数据
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        
        logger.info(f"用户 {user.id} 查询 {start_date} 到 {end_date} 的公司周报数据")
        
        # 获取公司汇总报告
        company_report = crm_statistics_service.aggregate_company_weekly_report(
            session=db_session,
            start_date=start_date,
            end_date=end_date
        )
        
        if not company_report:
            logger.warning(f"{start_date} 到 {end_date} 没有找到任何公司周报数据")
            return None
        
        logger.info(f"成功生成公司周报数据，日期范围: {start_date} 到 {end_date}")
        
        return company_report
        
    except Exception as e:
        logger.exception(f"查询公司周报数据失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"查询公司周报数据失败: {str(e)}"
        )


@router.post(
    "/crm/sales-task/department-weekly-summary",
    response_model=List[DepartmentSalesTaskWeeklySummaryResponse],
)
def get_department_sales_task_weekly_summary(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: SalesTaskWeeklySummaryRequest,
):
    """
    销售任务周报：按部门汇总（department_id）。

    数据来源：crm_todos_weekly_metrics（由定时任务写入）
    """
    try:
        if request.start_date and request.end_date:
            week_start = request.start_date
            week_end = request.end_date
        else:
            # 默认最近一周：与 send_sales_task_summary 口径一致（上周日到本周六）
            today = datetime.now().date()
            days_since_sunday = (today.weekday() + 1) % 7
            week_start = today - timedelta(days=days_since_sunday + 7)
            week_end = week_start + timedelta(days=6)

        logger.info(f"用户 {user.id} 查询销售任务部门周报，范围: {week_start} 到 {week_end}")

        reports = crm_sales_task_statistics_service.aggregate_department_weekly_summary(
            session=db_session,
            week_start=week_start,
            week_end=week_end,
            department_id=request.department_id,
        )
        return reports
    except Exception as e:
        logger.exception(f"查询销售任务部门周报失败: {e}")
        raise InternalServerError()


@router.post(
    "/crm/sales-task/company-weekly-summary",
    response_model=CompanySalesTaskWeeklySummaryResponse,
)
def get_company_sales_task_weekly_summary(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: SalesTaskWeeklySummaryRequest,
):
    """
    销售任务周报：公司总计（全公司）。

    数据来源：crm_todos_weekly_metrics（由定时任务写入）
    """
    try:
        if request.start_date and request.end_date:
            week_start = request.start_date
            week_end = request.end_date
        else:
            today = datetime.now().date()
            days_since_sunday = (today.weekday() + 1) % 7
            week_start = today - timedelta(days=days_since_sunday + 7)
            week_end = week_start + timedelta(days=6)

        logger.info(f"用户 {user.id} 查询销售任务公司周报，范围: {week_start} 到 {week_end}")

        report = crm_sales_task_statistics_service.aggregate_company_weekly_summary(
            session=db_session,
            week_start=week_start,
            week_end=week_end,
        )
        if not report:
            raise HTTPException(status_code=400, detail="未找到该周的销售任务周报数据")
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"查询销售任务公司周报失败: {e}")
        raise InternalServerError()


@router.post("/crm/customer-document/upload")
def upload_customer_document(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: CustomerDocumentUploadRequest,
):
    """
    上传客户文档
    
    支持飞书文档链接和本地文件路径，自动处理授权和内容读取。
    
    Args:
        request: 文档上传请求，包含文件类别、客户信息、文档链接等。
        
    Returns:
        文档上传响应，包含上传结果、文档ID或授权信息等。
    """
    try:
        customer_document_service = CustomerDocumentService()
        
        # 处理uploader_id类型转换和验证
        uploader_id = request.uploader_id
        if uploader_id:
            try:
                uploader_id = UUID(uploader_id)
                # 确保上传者ID与当前用户ID一致
                if uploader_id != user.id:
                    return {"code": 400, "message": "上传者ID必须与当前用户ID一致", "data": {}}
            except ValueError:
                return {"code": 400, "message": "uploader_id格式无效，应为有效的UUID", "data": {}}
        else:
            uploader_id = user.id
        
        # 上传客户文档
        result = customer_document_service.upload_customer_document(
            db_session=db_session,
            file_category=request.file_category,
            account_name=request.account_name,
            account_id=request.account_id,
            document_url=request.document_url,
            uploader_id=uploader_id,
            uploader_name=request.uploader_name or user.name or user.email,
            feishu_auth_code=request.feishu_auth_code
        )
        
        # 如果上传成功
        if result.get("success"):
            return {
                "code": 0,
                "message": "success",
                "data": {}
            }
        
        # 如果需要授权，返回401状态码
        if result.get("data", {}).get("auth_required"):
            data = result["data"]
            return {
                "code": 401,
                "message": result["message"],
                "data": data
            }
        
        # 其他错误情况
        return {
            "code": 400,
            "message": result["message"],
            "data": result.get("data", {})
        }
        
    except Exception as e:
        logger.exception(f"上传客户文档失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"上传客户文档失败: {str(e)}"
        )


@router.get("/crm/customer-documents")
def get_customer_documents(
    db_session: SessionDep,
    user: CurrentUserDep,
    account_id: Optional[str] = None,
    file_category: Optional[str] = None,
    uploader_id: Optional[str] = None,
    view_type: Optional[str] = "auto",  # auto, my, team, all
):
    """
    获取客户文档列表（根据用户权限自动过滤）
    
    权限规则：
    - 普通用户：只能查看自己上传的文档
    - 团队lead：可以查看本团队的所有文档
    - 超级管理员或管理员：可以查看所有文档
    
    Args:
        account_id: 客户ID（可选）
        file_category: 文件类别（可选）
        uploader_id: 上传者ID（可选，仅超级管理员或管理员可用）
        view_type: 视图类型
            - "auto": 根据用户权限自动选择（默认）
            - "my": 只查看自己的文档
            - "team": 查看团队文档（仅团队lead和超管可用）
            - "all": 查看所有文档（仅超管可用）
        
    Returns:
        根据权限过滤的客户文档列表
    """
    try:
        customer_document_service = CustomerDocumentService()
        user_profile_repo = UserProfileRepo()
        
        # 获取当前用户的部门信息
        user_profile = user_profile_repo.get_by_oauth_user_id(db_session, str(user.id))
        current_user_department = user_profile.department if user_profile else None
        
        # 检查是否为团队lead（没有直属上级且有部门名称的用户被认为是leader）
        is_team_lead = user_profile and not user_profile.direct_manager_id and user_profile.department
        
        # 检查是否为超级管理员或管理员
        is_superuser_or_admin = customer_document_service._is_superuser_or_admin(
            db_session=db_session,
            user_id=user.id,
            user_is_superuser=user.is_superuser,
            user_profile=user_profile
        )
        
        # 根据view_type和用户权限确定查询范围
        if view_type == "my":
            # 强制查看自己的文档
            documents = customer_document_service.get_customer_documents(
                db_session=db_session,
                uploader_id=str(user.id),
                file_category=file_category
            )
            user_role = "user"
            view_description = "我的文档"
            
        elif view_type == "team":
            # 查看团队文档
            if not is_team_lead and not is_superuser_or_admin:
                raise HTTPException(
                    status_code=403,
                    detail="只有团队lead和超级管理员可以查看团队文档"
                )
            
            if is_superuser_or_admin:
                # 管理员可以查看所有文档
                documents = customer_document_service.get_customer_documents(
                    db_session=db_session,
                    account_id=account_id,
                    file_category=file_category,
                    uploader_id=uploader_id
                )
                view_description = "所有团队文档"
            else:
                # 团队lead查看本团队文档
                team_members = user_profile_repo.get_department_members(db_session, current_user_department)
                team_member_ids = [str(member.oauth_user_id) for member in team_members if member.oauth_user_id]
                
                if not team_member_ids:
                    documents = []
                else:
                    statement = select(CustomerDocument).where(
                        or_(*[CustomerDocument.uploader_id == member_id for member_id in team_member_ids])
                    )
                    
                    if account_id:
                        statement = statement.where(CustomerDocument.account_id == account_id)
                    if file_category:
                        statement = statement.where(CustomerDocument.file_category == file_category)
                    
                    statement = statement.order_by(CustomerDocument.created_at.desc())
                    
                    documents = db_session.exec(statement).all()
                
                view_description = f"{current_user_department}团队文档"
            user_role = "team_lead" if is_team_lead else "superuser_or_admin"
            
        elif view_type == "all":
            # 查看所有文档（仅超管可用）
            if not is_superuser_or_admin:
                raise HTTPException(
                    status_code=403,
                    detail="只有超级管理员或管理员可以查看所有文档"
                )
            
            documents = customer_document_service.get_customer_documents(
                db_session=db_session,
                account_id=account_id,
                file_category=file_category,
                uploader_id=uploader_id
            )
            user_role = "superuser_or_admin"
            view_description = "所有文档"
            
        else:  # view_type == "auto" 或默认
            # 根据用户权限自动选择
            if is_superuser_or_admin:
                # 超管默认查看所有文档
                documents = customer_document_service.get_customer_documents(
                    db_session=db_session,
                    account_id=account_id,
                    file_category=file_category,
                    uploader_id=uploader_id
                )
                user_role = "superuser_or_admin"
                view_description = "所有文档"
                
            elif is_team_lead:
                # 团队lead默认查看本团队文档
                team_members = user_profile_repo.get_department_members(db_session, current_user_department)
                team_member_ids = [str(member.oauth_user_id) for member in team_members if member.oauth_user_id]
                
                if not team_member_ids:
                    documents = []
                else:
                    statement = select(CustomerDocument).where(
                        or_(*[CustomerDocument.uploader_id == member_id for member_id in team_member_ids])
                    )
                    
                    if account_id:
                        statement = statement.where(CustomerDocument.account_id == account_id)
                    if file_category:
                        statement = statement.where(CustomerDocument.file_category == file_category)
                    
                    statement = statement.order_by(CustomerDocument.created_at.desc())
                    
                    documents = db_session.exec(statement).all()
                
                user_role = "team_lead"
                view_description = f"{current_user_department}团队文档"
                
            else:
                # 普通用户默认查看自己的文档
                documents = customer_document_service.get_customer_documents(
                    db_session=db_session,
                    uploader_id=user.id,
                    file_category=file_category
                )
                user_role = "user"
                view_description = "我的文档"
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "documents": [
                    {
                        "id": doc.id,
                        "file_category": doc.file_category,
                        "account_name": doc.account_name,
                        "account_id": doc.account_id,
                        "document_url": doc.document_url,
                        "document_type": doc.document_type,
                        "document_title": doc.document_title,
                        "uploader_id": doc.uploader_id,
                        "uploader_name": doc.uploader_name,
                        "created_at": doc.created_at.isoformat(),
                        "updated_at": doc.updated_at.isoformat()
                    }
                    for doc in documents
                ],
                "total": len(documents),
                "user_role": user_role,
                "view_type": view_type,
                "view_description": view_description,
                "team_department": current_user_department if is_team_lead else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取客户文档列表失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"获取客户文档列表失败: {str(e)}"
        )


@router.get("/crm/customer-documents/{document_id}")
def get_customer_document(
    db_session: SessionDep,
    user: CurrentUserDep,
    document_id: int,
):
    """
    获取客户文档详情（根据用户权限）
    
    - 普通用户：只能查看自己上传的文档
    - 团队lead：可以查看本团队的所有文档
    - 超级管理员：可以查看所有文档
    
    Args:
        document_id: 文档ID
        
    Returns:
        客户文档详情
    """
    try:
        customer_document_service = CustomerDocumentService()
        user_profile_repo = UserProfileRepo()
        
        # 获取文档详情
        document = customer_document_service.get_customer_document_by_id(
            db_session=db_session,
            document_id=document_id
        )
        
        if not document:
            raise HTTPException(
                status_code=404,
                detail="文档不存在"
            )
        
        # 获取当前用户的部门信息
        user_profile = user_profile_repo.get_by_oauth_user_id(db_session, str(user.id))
        current_user_department = user_profile.department if user_profile else None
        
        # 权限检查
        # 检查是否为超级管理员或管理员
        is_superuser_or_admin = customer_document_service._is_superuser_or_admin(
            db_session=db_session,
            user_id=user.id,
            user_is_superuser=user.is_superuser,
            user_profile=user_profile
        )
        
        if is_superuser_or_admin:
            # 超级管理员或管理员可以查看所有文档
            pass
        else:
            # 检查是否为团队lead（没有直属上级且有部门名称的用户被认为是leader）
            is_team_lead = user_profile and not user_profile.direct_manager_id and user_profile.department
            
            if is_team_lead:
                # 团队lead可以查看本团队的文档
                team_members = user_profile_repo.get_department_members(db_session, current_user_department)
                team_member_ids = [str(member.oauth_user_id) for member in team_members if member.oauth_user_id]
                
                if document.uploader_id not in team_member_ids:
                    raise HTTPException(
                        status_code=403,
                        detail="无权访问此文档"
                    )
            else:
                # 普通用户只能查看自己上传的文档
                if document.uploader_id != user.id:
                    raise HTTPException(
                        status_code=403,
                        detail="无权访问此文档"
                    )
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "id": document.id,
                "file_category": document.file_category,
                "account_name": document.account_name,
                "account_id": document.account_id,
                "document_url": document.document_url,
                "document_type": document.document_type,
                "document_title": document.document_title,
                "uploader_id": document.uploader_id,
                "uploader_name": document.uploader_name,
                "created_at": document.created_at.isoformat(),
                "updated_at": document.updated_at.isoformat(),
                "document_content_id": document.document_content_id
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取客户文档详情失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"获取客户文档详情失败: {str(e)}"
        )


