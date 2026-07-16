"""CRM 周跟进 HTTP 路由。"""
import logging
import io
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlmodel import distinct, func, or_, select
from sqlalchemy import false

from app.api.deps import CurrentUserDep, SessionDep
from app.repositories.department_mirror import department_mirror_repo
from app.api.routes.crm.models import (
    AccountTagOptionOut,
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
from app.repositories.crm_account import crm_account_repo
from app.repositories.department_mirror import department_mirror_repo
from app.repositories.user_department_relation import user_department_relation_repo
from app.repositories.user_profile import UserProfileRepo
from app.repositories.visit_record import visit_record_repo
from app.services.crm_config_service import get_resolved_field_mapping
from app.services.crm_weekly_followup_engagement_service import crm_weekly_followup_engagement_service
from app.utils.crm_weekly_followup_week_boundary import format_weekly_followup_period
from app.services.oauth_service import oauth_client
from app.permissions.weekly_followup_permission_service import weekly_followup_permission_service
from app.utils.crm_account_tags import parse_account_tags
from app.utils.crm_comments import CRMCommentValidationError, merge_append_crm_comments
from app.utils.crm_followup_object import FOLLOWUP_OBJECT_TYPES
from app.utils.crm_followup_object_type import resolve_customer_attribute_display_label_for_object

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/weekly-followup"])


def _resolve_user_department(
    db_session: SessionDep, user: CurrentUserDep
) -> tuple[Optional[str], Optional[str]]:
    """本部门 id/name：优先 user_department_relation，部门名兜底 profiles。"""
    user_profile_repo = UserProfileRepo()
    profile = user_profile_repo.get_by_user_id(db_session, user.id)
    dept_name = profile.department if profile else None
    dept_id = user_department_relation_repo.get_primary_department_by_user_ids(
        db_session,
        [str(user.id)],
    ).get(str(user.id))
    return dept_id, dept_name


def _can_view_weekly_followup(db_session: SessionDep, user: CurrentUserDep) -> tuple[bool, bool, Optional[str], Optional[str]]:
    """
    Returns: (can_view, is_company_admin, user_department_id, user_department_name)
    """
    dept_id, dept_name = _resolve_user_department(db_session, user)
    user_profile_repo = UserProfileRepo()
    profile = user_profile_repo.get_by_user_id(db_session, user.id)

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
                    id=str(item.get("id") or "") or None,
                    reply_to_id=str(item.get("reply_to_id") or "") or None,
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


def _resolve_weekly_followup_department_scope(
    db_session: SessionDep,
    scope: str,
    *,
    is_company_admin: bool,
    user_dept_id: Optional[str],
    user_dept_name: Optional[str],
    department_id: Optional[str],
    department_name: Optional[str],
) -> tuple[Optional[str], Optional[str], List[str]]:
    dept_id = None
    dept_name = None
    subtree_dept_ids: List[str] = []
    if scope != "department":
        return dept_id, dept_name, subtree_dept_ids

    if is_company_admin:
        dept_id = (department_id or "").strip() or None
        dept_name = (department_name or "").strip() or None
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
    return dept_id, dept_name, subtree_dept_ids


def _build_weekly_followup_entity_base_conds(
    *,
    week_start,
    week_end,
    scope: str,
    user_id: UUID,
    dept_id: Optional[str],
    dept_name: Optional[str],
    subtree_dept_ids: List[str],
    is_sales_limited: bool,
) -> list:
    conds = [
        CRMWeeklyFollowupEntitySummary.week_start == week_start,
        CRMWeeklyFollowupEntitySummary.week_end == week_end,
    ]
    if scope == "my":
        conds.append(CRMWeeklyFollowupEntitySummary.owner_user_id == str(user_id))
    elif scope == "department":
        if subtree_dept_ids:
            conds.append(CRMWeeklyFollowupEntitySummary.department_id.in_(subtree_dept_ids))
        elif dept_id:
            conds.append(CRMWeeklyFollowupEntitySummary.department_id == dept_id)
        elif dept_name:
            conds.append(CRMWeeklyFollowupEntitySummary.department_name == dept_name)
        if is_sales_limited:
            conds.append(CRMWeeklyFollowupEntitySummary.owner_user_id == str(user_id))
    return conds


def _append_weekly_followup_entity_filters(
    db_session: SessionDep,
    conds: list,
    payload: WeeklyFollowupDetailQueryIn,
) -> None:
    if payload.filter_department_name:
        filter_dept_names = list({name.strip() for name in payload.filter_department_name if name and name.strip()})
        if filter_dept_names:
            conds.append(CRMWeeklyFollowupEntitySummary.department_name.in_(filter_dept_names))

    if payload.filter_owner_name:
        filter_owner_names = list({name.strip() for name in payload.filter_owner_name if name and name.strip()})
        if filter_owner_names:
            conds.append(CRMWeeklyFollowupEntitySummary.owner_name.in_(filter_owner_names))

    account_conds = []
    if payload.filter_account_id:
        filter_account_id = payload.filter_account_id.strip()
        if filter_account_id:
            account_conds.append(CRMWeeklyFollowupEntitySummary.followup_object_id == filter_account_id)

    if payload.filter_account_name:
        filter_account_name = payload.filter_account_name.strip()
        if filter_account_name:
            account_conds.append(CRMWeeklyFollowupEntitySummary.followup_object_name == filter_account_name)

    if account_conds:
        conds.append(or_(*account_conds) if len(account_conds) > 1 else account_conds[0])

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
        conds.append(or_(*opportunity_conds) if len(opportunity_conds) > 1 else opportunity_conds[0])

    if payload.filter_customer_attribute:
        selected_attrs = {
            value.strip()
            for value in payload.filter_customer_attribute
            if value and value.strip() and value.strip() in FOLLOWUP_OBJECT_TYPES
        }
        if selected_attrs:
            conds.append(CRMWeeklyFollowupEntitySummary.followup_object_type.in_(list(selected_attrs)))
        else:
            # 传入了筛选值但无一合法：结果为空，避免静默忽略
            conds.append(false())

    if payload.filter_tag_ids:
        tag_ids = list({tag_id.strip() for tag_id in payload.filter_tag_ids if tag_id and tag_id.strip()})
        if tag_ids:
            scoped_account_ids = _list_followup_account_ids_for_entity_conds(db_session, conds)
            matching_account_ids = crm_account_repo.get_account_unique_ids_by_tag_ids(
                db_session,
                tag_ids,
                account_ids=scoped_account_ids,
            )
            if matching_account_ids:
                conds.append(CRMWeeklyFollowupEntitySummary.followup_object_id.in_(matching_account_ids))
            else:
                conds.append(false())

    if payload.filter_forecast_amount_min is not None:
        conds.append(CRMWeeklyFollowupEntitySummary.forecast_amount >= payload.filter_forecast_amount_min)
    if payload.filter_forecast_amount_max is not None:
        conds.append(CRMWeeklyFollowupEntitySummary.forecast_amount <= payload.filter_forecast_amount_max)
    if payload.filter_expected_closing_date_start is not None:
        conds.append(
            CRMWeeklyFollowupEntitySummary.expected_closing_date
            >= payload.filter_expected_closing_date_start
        )
    if payload.filter_expected_closing_date_end is not None:
        conds.append(
            CRMWeeklyFollowupEntitySummary.expected_closing_date
            <= payload.filter_expected_closing_date_end
        )


def _weekly_followup_entities_to_row_out(
    db_session: SessionDep,
    entities: list[CRMWeeklyFollowupEntitySummary],
    *,
    include_comments: bool,
) -> list[WeeklyFollowupEntityRowOut]:
    from app.utils.crm_followup_object import FOLLOWUP_OBJECT_TYPE_LEAD

    followup_ids: list[str] = []
    for entity in entities:
        ftype = (getattr(entity, "followup_object_type", None) or "").strip()
        fid = (getattr(entity, "followup_object_id", None) or "").strip()
        if fid and ftype != FOLLOWUP_OBJECT_TYPE_LEAD:
            followup_ids.append(fid)

    field_mapping = get_resolved_field_mapping(db_session, report_type="周跟进实体明细")

    crm_by_id: dict[str, object] = {}
    if followup_ids:
        for account in crm_account_repo.get_by_account_ids(db_session, list(dict.fromkeys(followup_ids))):
            uid = str(getattr(account, "unique_id", "") or "").strip()
            if uid:
                crm_by_id[uid] = account

    rows: list[WeeklyFollowupEntityRowOut] = []
    for entity in entities:
        ftype = (getattr(entity, "followup_object_type", None) or "").strip() or None
        fid = (getattr(entity, "followup_object_id", None) or "").strip() or None
        fname = (getattr(entity, "followup_object_name", None) or "").strip() or None

        from app.utils.crm_followup_object import FollowupObject
        followup_obj = FollowupObject(object_type=ftype, object_id=fid, object_name=fname) if ftype and fid else None

        crm_account_join_id = fid if ftype and ftype != FOLLOWUP_OBJECT_TYPE_LEAD and fid else None
        crm_account = crm_by_id.get(crm_account_join_id or "") if crm_account_join_id else None
        customer_attribute = resolve_customer_attribute_display_label_for_object(
            followup_obj,
            field_mapping,
        )
        tag_options: list[AccountTagOptionOut] = []
        if crm_account is not None:
            tag_options = [
                AccountTagOptionOut(id=tag.id, name=tag.name)
                for tag in parse_account_tags(
                    getattr(crm_account, "extra", None)
                    if isinstance(getattr(crm_account, "extra", None), dict)
                    else None
                )
            ]

        rows.append(
            WeeklyFollowupEntityRowOut(
                id=entity.id,
                department_name=entity.department_name,
                account_id=entity.account_id,
                account_name=entity.account_name,
                opportunity_id=entity.opportunity_id,
                opportunity_name=entity.opportunity_name,
                partner_id=entity.partner_id,
                partner_name=entity.partner_name,
                followup_object_type=followup_obj.object_type if followup_obj else None,
                followup_object_name=followup_obj.object_name if followup_obj else None,
                followup_object_id=followup_obj.object_id if followup_obj else None,
                customer_attribute=customer_attribute,
                tags=tag_options,
                owner_name=entity.owner_name,
                progress=entity.progress,
                risks=entity.risks,
                forecast_amount=entity.forecast_amount,
                expected_closing_date=entity.expected_closing_date,
                comments=_to_comments(entity.comments) if include_comments else [],
            )
        )
    return rows


def _list_followup_account_ids_for_entity_conds(db_session: SessionDep, conds: list) -> list[str]:
    """end_customer/partner 的 followup_object_id（可 join crm_accounts）；排除 lead。"""
    rows = db_session.exec(
        select(distinct(CRMWeeklyFollowupEntitySummary.followup_object_id))
        .where(
            *conds,
            CRMWeeklyFollowupEntitySummary.followup_object_id.isnot(None),
            func.trim(CRMWeeklyFollowupEntitySummary.followup_object_id) != "",
            or_(
                CRMWeeklyFollowupEntitySummary.followup_object_type.is_(None),
                CRMWeeklyFollowupEntitySummary.followup_object_type != "lead",
            ),
        )
    ).all()
    return [str(fid).strip() for fid in rows if fid and str(fid).strip()]


@router.post("/crm/weekly-followup/detail")
def get_weekly_followup_detail(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: WeeklyFollowupDetailQueryIn,
) -> WeeklyFollowupDetailOut:
    """
    查询单次周总结详情（整体总结 + scope 下实体明细列表）
    """
    week_start = payload.start_date
    week_end = payload.end_date
    period = format_weekly_followup_period(week_end)

    can_view_team, is_company_admin, user_dept_id, user_dept_name = _can_view_weekly_followup(db_session, user)

    scope = payload.scope
    if scope == "company" and not is_company_admin:
        raise HTTPException(status_code=403, detail="权限不足：仅公司管理员可查看 company scope")
    # department scope：团队负责人/管理员可看全团队；普通销售允许访问，但仅返回“自己负责”的明细行
    # 详情页明细列表需要完整展示（包含评论）
    include_comments = True
    is_sales_limited = bool(scope == "department" and (not is_company_admin) and (not can_view_team))

    page = max(int(payload.page or 1), 1)
    size = max(min(int(payload.size or 50), 200), 1)
    offset = (page - 1) * size

    dept_id, dept_name, subtree_dept_ids = _resolve_weekly_followup_department_scope(
        db_session,
        scope,
        is_company_admin=is_company_admin,
        user_dept_id=user_dept_id,
        user_dept_name=user_dept_name,
        department_id=payload.department_id,
        department_name=payload.department_name,
    )

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
    conds = _build_weekly_followup_entity_base_conds(
        week_start=week_start,
        week_end=week_end,
        scope=scope,
        user_id=user.id,
        dept_id=dept_id,
        dept_name=dept_name,
        subtree_dept_ids=subtree_dept_ids,
        is_sales_limited=is_sales_limited,
    )
    _append_weekly_followup_entity_filters(db_session, conds, payload)

    total = db_session.exec(select(func.count()).select_from(CRMWeeklyFollowupEntitySummary).where(*conds)).one()
    entities = db_session.exec(
        select(CRMWeeklyFollowupEntitySummary)
        .where(*conds)
        .order_by(CRMWeeklyFollowupEntitySummary.department_name, CRMWeeklyFollowupEntitySummary.owner_name, CRMWeeklyFollowupEntitySummary.updated_at.desc())
        .offset(offset)
        .limit(size)
    ).all()

    items = _weekly_followup_entities_to_row_out(
        db_session, entities, include_comments=include_comments
    )

    return WeeklyFollowupDetailOut(
        scope=scope,
        period=period,
        week_start=week_start,
        week_end=week_end,
        summary=summary_out,
        entities=WeeklyFollowupEntityPageOut(total=int(total or 0), page=page, size=size, items=items),
    )


@router.post("/crm/weekly-followup/detail/export")
def export_weekly_followup_detail(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: WeeklyFollowupDetailQueryIn,
):
    """
    导出单次周总结详情的明细列表（entities）为 XLSX。
    复用 /crm/weekly-followup/detail 的权限、scope 与筛选逻辑。
    """
    try:
        wb = Workbook()
        ws_entities = wb.active
        ws_entities.title = "entities"
        ws_entities.append(
            [
                "department_name",
                "followup_object_type",
                "followup_object_name",
                "followup_object_id",
                "customer_attribute",
                "object_tags",
                "account_id",
                "account_name",
                "opportunity_id",
                "opportunity_name",
                "partner_id",
                "partner_name",
                "owner_name",
                "progress",
                "risks",
                "forecast_amount",
                "expected_closing_date",
                "comments",
            ]
        )

        page = 1
        page_size = 200
        total = 0

        while True:
            query_payload = payload.model_copy(update={"page": page, "size": page_size})
            detail = get_weekly_followup_detail(
                db_session=db_session,
                user=user,
                payload=query_payload,
            )

            if page == 1:
                total = int(detail.entities.total or 0)

            for item in detail.entities.items:
                comments_text = "\n".join(
                    [
                        f"{c.author or ''}({c.type or ''}): {c.content or ''}"
                        for c in (item.comments or [])
                    ]
                )
                object_tags_text = ", ".join(
                    tag.name for tag in (item.tags or []) if tag.name
                )
                ws_entities.append(
                    [
                        item.department_name or "",
                        item.followup_object_type or "",
                        item.followup_object_name or "",
                        item.followup_object_id or "",
                        item.customer_attribute or "",
                        object_tags_text,
                        item.account_id or "",
                        item.account_name or "",
                        item.opportunity_id or "",
                        item.opportunity_name or "",
                        item.partner_id or "",
                        item.partner_name or "",
                        item.owner_name or "",
                        item.progress or "",
                        item.risks or "",
                        item.forecast_amount if item.forecast_amount is not None else "",
                        item.expected_closing_date.isoformat() if item.expected_closing_date else "",
                        comments_text,
                    ]
                )

            if page * page_size >= total or not detail.entities.items:
                break
            page += 1

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        scope_text = (payload.scope or "all").strip()
        filename = (
            "weekly_followup_detail_"
            f"{scope_text}_"
            f"{payload.start_date.isoformat()}_{payload.end_date.isoformat()}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        )

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail="导出周跟进总结失败")


@router.post("/crm/weekly-followup/detail/filter-options")
def get_weekly_followup_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: WeeklyFollowupFilterOptionsQueryIn,
) -> WeeklyFollowupFilterOptionsOut:
    """
    获取周总结详情页的筛选选项（部门名称、负责人名称、客户 tags）
    用于前端下拉选择框填充
    """
    week_start = payload.start_date
    week_end = payload.end_date

    can_view_team, is_company_admin, user_dept_id, user_dept_name = _can_view_weekly_followup(db_session, user)

    scope = payload.scope
    if scope == "company" and not is_company_admin:
        raise HTTPException(status_code=403, detail="权限不足：仅公司管理员可查看 company scope")

    is_sales_limited = bool(scope == "department" and (not is_company_admin) and (not can_view_team))

    dept_id, dept_name, subtree_dept_ids = _resolve_weekly_followup_department_scope(
        db_session,
        scope,
        is_company_admin=is_company_admin,
        user_dept_id=user_dept_id,
        user_dept_name=user_dept_name,
        department_id=payload.department_id,
        department_name=payload.department_name,
    )

    conds = _build_weekly_followup_entity_base_conds(
        week_start=week_start,
        week_end=week_end,
        scope=scope,
        user_id=user.id,
        dept_id=dept_id,
        dept_name=dept_name,
        subtree_dept_ids=subtree_dept_ids,
        is_sales_limited=is_sales_limited,
    )

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

    followup_crm_ids = _list_followup_account_ids_for_entity_conds(db_session, conds)
    tag_options = crm_account_repo.list_distinct_tags_by_account_ids(db_session, followup_crm_ids)

    return WeeklyFollowupFilterOptionsOut(
        department_names=[name for name in department_names if name],
        owner_names=[name for name in owner_names if name],
        tags=[AccountTagOptionOut(id=tag.id, name=tag.name) for tag in tag_options],
    )


@router.post("/crm/weekly-followup/trigger")
def trigger_weekly_followup_summary_task(
    db_session: SessionDep,
    # user: CurrentUserDep,
    payload: WeeklyFollowupTriggerTaskIn = Body(default=WeeklyFollowupTriggerTaskIn()),
) -> WeeklyFollowupTriggerTaskOut:
    """
    人工触发“周跟进总结”生成任务（异步，返回 task_id）。
    - 暂时不做权限校验，方便测试
    - start_date/end_date 可不传；不传时任务内部按默认口径计算（上周六-本周五，北京时间）
    - department_id 可选；指定时仅生成该部门（含其子部门负责人跟进）的部门级总结
    """
    # _, is_company_admin, _, _ = _can_view_weekly_followup(db_session, user)
    # if not is_company_admin:
    #     raise HTTPException(status_code=403, detail="权限不足：仅公司管理员可触发生成任务")

    start_date = payload.start_date
    end_date = payload.end_date
    if (start_date is None) != (end_date is None):
        raise HTTPException(status_code=400, detail="start_date/end_date 需要同时传或同时不传")

    department_id = (payload.department_id or "").strip() or None
    if department_id:
        dept_name = department_mirror_repo.get_department_name_by_id(db_session, department_id)
        if not dept_name:
            raise HTTPException(status_code=400, detail=f"部门不存在: {department_id}")

    scopes = (payload.scopes or "").strip() or None
    if department_id:
        if scopes and scopes.lower() == "company":
            raise HTTPException(
                status_code=400,
                detail="指定 department_id 时不能仅生成公司级总结（scopes=company）",
            )
        scopes = scopes or "department"
    else:
        scopes = scopes or "all"

    # 延迟导入，避免路由模块加载时引入 Celery task 依赖
    from app.tasks.cron_jobs import generate_crm_weekly_followup_summary

    task = generate_crm_weekly_followup_summary.delay(
        start_date_str=start_date.isoformat() if start_date else None,
        end_date_str=end_date.isoformat() if end_date else None,
        scopes=scopes,
        department_id=department_id,
    )
    return WeeklyFollowupTriggerTaskOut(
        task_id=task.id,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
        scopes=scopes,
        status="PENDING",
    )


@router.post("/crm/weekly-followup/leader-engagement/trigger")
def trigger_weekly_followup_leader_engagement_report_task(
    payload: WeeklyFollowupTriggerTaskIn = Body(default=WeeklyFollowupTriggerTaskIn()),
) -> WeeklyFollowupTriggerTaskOut:
    """
    人工触发“周跟进总结 leader 阅读/互动统计推送”任务（异步，返回 task_id）。
    - 暂时不做权限校验，方便测试
    - start_date/end_date 可不传；不传时任务内部按默认口径计算（上周六-本周五，北京时间）
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

    权限：
    - OAuth ``sales:weekly_followup:view`` 功能门控
    - ``company`` scope：需 data-scope global（经营层/管理员等）
    - ``department`` scope：非 global 用户强制本部门；global 用户可按入参筛选部门
    """
    if not weekly_followup_permission_service.gate_view(db_session, user.id):
        raise HTTPException(status_code=403, detail="无周跟进总结查看权限")

    user_dept_id, user_dept_name = _resolve_user_department(db_session, user)
    is_global_scope = weekly_followup_permission_service.has_global_data_scope(db_session, user.id)

    scope = payload.scope
    if scope == "company" and not is_global_scope:
        raise HTTPException(status_code=403, detail="权限不足：仅公司级数据范围可查看 company scope")

    page = max(int(payload.page or 1), 1)
    size = max(min(int(payload.page_size or 20), 200), 1)
    offset = (page - 1) * size

    dept_id = None
    dept_name = None
    if scope == "department":
        if is_global_scope:
            dept_id = (payload.department_id or "").strip() or None
            dept_name = (payload.department_name or "").strip() or None
        else:
            # 非公司级范围：强制本部门
            dept_id = user_dept_id
            dept_name = user_dept_name
        if (not is_global_scope) and (dept_id is None and dept_name is None):
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
    追加保存周跟进实体评论（comments，JSON 数组）；请求体只需传本次新增条目。
    - 每条评论的 author_id 须与当前登录用户一致，否则返回 400
    - 支持 reply_to_id 回复已有评论；无效 reply_to_id 返回 400
    - type=task 时 id 可选由前端传入，未传则保持为空（不生成）；type=comment 时 id 由服务端生成
    - 保存成功后：type=comment 的顶层评论向负责人推送提醒；带 reply_to_id 的回复向被回复评论作者推送提醒（type=task 不推送）
    """
    # can_edit, is_company_admin, user_dept_id, user_dept_name = _can_edit_weekly_followup_comments(db_session, user)
    # if not can_edit:
    #     raise HTTPException(status_code=403, detail="权限不足：仅团队负责人或管理者可编辑评论")

    current_user_id = str(getattr(user, "id", "") or "")
    for c in payload.comments or []:
        if str(c.author_id or "").strip() != current_user_id:
            raise HTTPException(
                status_code=400,
                detail="存在 author_id 与当前登录用户不一致的评论，禁止代他人提交；请仅附加以本人身份发表的评论。",
            )

    entity = db_session.exec(select(CRMWeeklyFollowupEntitySummary).where(CRMWeeklyFollowupEntitySummary.id == entity_id)).first()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity summary not found")

    # if not is_company_admin:
    #     if user_dept_id and getattr(entity, "department_id", None) and entity.department_id != user_dept_id:
    #         raise HTTPException(status_code=403, detail="权限不足：只能编辑本团队记录")
    #     if not user_dept_id and user_dept_name and entity.department_name != user_dept_name:
    #         raise HTTPException(status_code=403, detail="权限不足：只能编辑本团队记录")

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))

    try:
        merged, appended = merge_append_crm_comments(
            entity.comments,
            [
                {
                    **c.model_dump(),
                    "author": c.author or "",
                    "type": c.type or "comment",
                }
                for c in (payload.comments or [])
            ],
            current_user_id,
            now=now_bj,
        )
    except CRMCommentValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    entity.comments = merged
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)

    # leader 参与度：若当前用户是团队负责人，且本次确实追加了条目，则记录 commented_at
    try:
        if appended:
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

    # 保存评论成功后推送提醒（不影响主流程，失败仅记录日志）
    try:
        from app.services.platform_notification_service import platform_notification_service

        owner_user_id = str(getattr(entity, "owner_user_id", "") or "")
        saved_comments = entity.comments if isinstance(entity.comments, list) else []
        comment_by_id: dict[str, dict] = {}
        for raw in saved_comments:
            if not isinstance(raw, dict):
                continue
            cid = str(raw.get("id") or "").strip()
            if cid:
                comment_by_id[cid] = raw

        from app.utils.push_page_urls import build_weekly_followup_summary_page_url

        week_part = f"{entity.week_start.isoformat()}~{entity.week_end.isoformat()}"
        dept_name = (entity.department_name or "").strip()
        jump_url = build_weekly_followup_summary_page_url(
            week_start=entity.week_start.isoformat(),
            week_end=entity.week_end.isoformat(),
            department_name=dept_name or None,
        )
        link_text = f"{entity.account_name or entity.partner_name}  {entity.opportunity_name}".strip()

        for item in payload.comments or []:
            item_type = str(item.type or "comment").strip().lower()
            if item_type != "comment":
                continue

            comment_preview = str(item.content or "").strip()
            if len(comment_preview) > 200:
                comment_preview = comment_preview[:197] + "..."

            author_name = str(item.author or "").strip()
            author_name = author_name or str(getattr(user, "name", "") or "").strip()
            author_name = author_name or "有人"

            reply_to_id = str(item.reply_to_id or "").strip()
            if reply_to_id:
                parent = comment_by_id.get(reply_to_id)
                recipient_user_id = str((parent or {}).get("author_id") or "").strip()
                if not recipient_user_id or recipient_user_id == current_user_id:
                    continue
                text = (
                    f"{author_name}回复了你的评论\n"
                    f"[{link_text}]({jump_url})\n"
                    f"周总结（{week_part}）\n"
                    f"回复：{comment_preview or '--'}\n"
                )
            else:
                recipient_user_id = owner_user_id
                if not recipient_user_id or recipient_user_id == current_user_id:
                    continue
                text = (
                    f"{author_name}评论了你的周跟进总结（{week_part}）\n"
                    f"[{link_text}]({jump_url})\n"
                    f"评论：{comment_preview or '--'}\n"
                )

            platform_notification_service.send_weekly_followup_comment_notification(
                db_session,
                recipient_user_id=recipient_user_id,
                message_text=text,
            )
    except Exception as e:
        logger.warning(f"发送周跟进评论提醒失败（不影响保存评论）：{e}")

    rows = _weekly_followup_entities_to_row_out(db_session, [entity], include_comments=True)
    return rows[0]
