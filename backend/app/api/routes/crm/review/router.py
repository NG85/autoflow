"""CRM 复盘（Review）HTTP 路由；路径均为完整 ``/crm/...`` URL。"""

import json
import logging
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlmodel import distinct, func, select

from app.api.deps import CurrentUserDep, OptionalUserDep, SessionDep
from app.api.routes.crm.models import (
    MyLatestReviewSessionOut,
    ReviewBranchSnapshotMergeFromCacheOut,
    ReviewBranchSnapshotSubmitIn,
    ReviewBranchSnapshotSubmitOut,
    ReviewOppBranchSnapshotsQueryIn,
    ReviewSessionForecastRecalcOut,
    ReviewSessionHistoryItemOut,
    ReviewSessionHistoryListOut,
    ReviewSessionInsightDetailOut,
    ReviewSessionInsightItemBaseOut,
    ReviewSessionInsightItemOut,
    ReviewSessionInsightRiskOpportunityOut,
    ReviewSessionInsightsBasicOut,
    ReviewSessionInsightsOut,
    ReviewSessionKpiMetricOut,
    ReviewSessionKpiMetricsOut,
    ReviewSessionPhaseUpdateIn,
    ReviewSessionProgressCategoryGroupBasicOut,
    ReviewSessionProgressCategoryGroupOut,
    ReviewSessionRiskInsightItemBasicOut,
    ReviewSnapshotFilterEnumsOut,
    ReviewSnapshotGroupDataQueryIn,
    ReviewSnapshotGroupsOut,
    ReviewSnapshotGroupsQueryIn,
)
from app.models.chat import ChatType
from app.models.crm_review import (
    CRMReviewAttendee,
    CRMReviewOppRiskProgress,
    CRMReviewRiskOpportunityRelation,
    CRMReviewSession,
)
from app.models.crm_system_configurations import CRMSystemConfiguration
from app.rag.chat.chat_flow import ChatFlow
from app.rag.chat.chat_service import get_final_chat_result
from app.rag.types import CrmDataType
from app.repositories.crm_review_attendee import crm_review_attendee_repo
from app.repositories.crm_review_kpi_metrics import crm_review_kpi_metrics_repo
from app.repositories.crm_review_session import crm_review_session_repo
from app.services.crm_review_service import crm_review_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/review"])


@router.post("/crm/review/sessions/{session_id}/my-opp-branch-snapshots")
def query_my_review_opp_branch_snapshots(
    session_id: str,
    request: ReviewOppBranchSnapshotsQueryIn,
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    商机快照分页列表（不分组）。
    - 返回结构与 ``snapshot-group-data`` 一致，只是没有 ``group_by`` / ``group_key``。
    - 可见范围：普通成员只看本人；负责人看本次 review 的全部成员。支持筛选、排序、字段级别；``sorts`` 未传或空时默认：负责人 → 预测类型 → 金额（降序）。
    - 排序：请求体 ``sorts`` 为按优先级排列的多字段排序。
    - 需要 session 信息、提交统计时请先调 ``snapshot-groups``。
    """
    sorts_arg = (
        [(s.field, s.direction) for s in request.sorts] if request.sorts is not None else None
    )
    return crm_review_service.get_my_edit_page_data(
        db_session,
        session_id=session_id,
        user_id=str(user.id),
        page=request.page,
        size=request.size,
        fields_level=request.fields_level,
        sorts=sorts_arg,
        snapshot_filters=request.snapshot_filters,
    )


@router.post("/crm/review/sessions/{session_id}/snapshot-groups")
def query_review_snapshot_groups(
    session_id: str,
    request: ReviewSnapshotGroupsQueryIn,
    db_session: SessionDep,
    user: CurrentUserDep,
) -> ReviewSnapshotGroupsOut:
    """
    分组汇总：各分组的 key、名称、数量，以及本次 review 的信息、提交统计、是否可编辑等（不含明细行）。
    - 可见范围：成员只看自己；负责人看本次 review 的全部成员。
    - ``group_by``：owner / forecast_type / opportunity_stage。
    - 可先筛选再分组；``sorts`` 仅第一项用于分组行顺序，未传或空则按分组键升序。
    """
    sorts_arg = (
        [(s.field, s.direction) for s in request.sorts] if request.sorts is not None else None
    )
    data = crm_review_service.list_snapshot_groups(
        db_session,
        session_id=session_id,
        user_id=str(user.id),
        group_by=request.group_by,
        sorts=sorts_arg,
        snapshot_filters=request.snapshot_filters,
    )
    return ReviewSnapshotGroupsOut.model_validate(data)


@router.post("/crm/review/sessions/{session_id}/snapshot-group-data")
def query_review_snapshot_group_data(
    session_id: str,
    request: ReviewSnapshotGroupDataQueryIn,
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    某一分组下的商机快照分页列表。可见范围同 ``snapshot-groups``。
    - ``group_key``：按负责人传 owner_id；按预测/阶段传字段值，空值用 ``__EMPTY__``。
    - 支持筛选、排序、字段级别；``sorts`` 未传或空时默认：负责人 → 预测类型 → 金额（降序）。
    """
    sorts_arg = (
        [(s.field, s.direction) for s in request.sorts] if request.sorts is not None else None
    )
    return crm_review_service.query_snapshot_group_data(
        db_session,
        session_id=session_id,
        user_id=str(user.id),
        group_by=request.group_by,
        group_key=request.group_key,
        page=request.page,
        size=request.size,
        fields_level=request.fields_level,
        sorts=sorts_arg,
        snapshot_filters=request.snapshot_filters,
    )


@router.get("/crm/review/sessions/{session_id}/opportunities/{opportunity_id}/risk-progress")
def query_review_opportunity_risk_progress_details(
    session_id: str,
    opportunity_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    单个商机详情：风险/进展/机会摘要/机会诉求洞察明细（条数 + 列表）+ snapshot 基础信息。
    record_type 包含：RISK、PROGRESS、OPP_SUMMARY、OPP_REQS_INSIGHT。
    仅当该商机在本次 review 对你可见时返回，否则 404。
    """
    return crm_review_service.get_opportunity_risk_progress_details(
        db_session,
        session_id=session_id,
        user_id=str(user.id),
        opportunity_id=opportunity_id,
    )


@router.get("/crm/opportunities/{opportunity_id}/detail")
def query_review_opportunity_detail(
    opportunity_id: str,
    db_session: SessionDep,
    _user: CurrentUserDep,
    session_id: Optional[str] = Query(default=None, description="可选：指定 review session_id"),
):
    """
    查询指定商机详情（风险/进展/机会摘要/机会诉求洞察 + snapshot 基础信息）。
    - 传 ``session_id``：按指定 review session 查询。
    - 不传 ``session_id``：自动使用该商机关联的最新 review session。
    - ``snapshot_basic`` 含 ``opportunity_id``、``account_id``（客户）等字段。
    不校验当前用户是否是该最新 session 的参会人。
    """
    return crm_review_service.get_opportunity_risk_progress_details_by_latest_session(
        db_session,
        opportunity_id=opportunity_id,
        session_id=session_id,
    )


@router.get("/crm/review/my/latest-session")
def query_my_latest_review_session(
    db_session: SessionDep,
    user: CurrentUserDep,
) -> MyLatestReviewSessionOut:
    """
    当前用户参与的、汇报日最新的一场 review 的 session id；没有则为 null。
    """
    row = db_session.exec(
        select(CRMReviewSession.unique_id)
        .join(
            CRMReviewAttendee,
            CRMReviewAttendee.session_id == CRMReviewSession.unique_id,
        )
        .where(CRMReviewAttendee.user_id == str(user.id))
        .order_by(CRMReviewSession.report_date.desc(), CRMReviewSession.create_time.desc())
        .limit(1)
    ).first()
    return MyLatestReviewSessionOut(review_session_id=str(row) if row else None)


@router.get("/crm/review/my/sessions/history")
def query_my_review_session_history(
    db_session: SessionDep,
    user: CurrentUserDep,
    page: int = 1,
    size: int = 20,
) -> ReviewSessionHistoryListOut:
    """
    当前用户参与过的 review 列表（分页），从新到旧。``size`` 最大 200。
    """
    page = max(int(page or 1), 1)
    size = max(min(int(size or 20), 200), 1)
    offset = (page - 1) * size

    total = int(
        db_session.exec(
            select(func.count(distinct(CRMReviewSession.unique_id)))
            .select_from(CRMReviewSession)
            .join(
                CRMReviewAttendee,
                CRMReviewAttendee.session_id == CRMReviewSession.unique_id,
            )
            .where(CRMReviewAttendee.user_id == str(user.id))
        ).one()
        or 0
    )

    rows = db_session.exec(
        select(CRMReviewSession)
        .join(
            CRMReviewAttendee,
            CRMReviewAttendee.session_id == CRMReviewSession.unique_id,
        )
        .where(CRMReviewAttendee.user_id == str(user.id))
        .order_by(CRMReviewSession.report_date.desc(), CRMReviewSession.create_time.desc())
        .offset(offset)
        .limit(size)
    ).all()

    items: List[ReviewSessionHistoryItemOut] = [
        ReviewSessionHistoryItemOut(
            session_id=str(r.unique_id),
            session_name=r.session_name,
            department_name=r.department_name,
            period=str(r.period),
            period_start=r.period_start,
            period_end=r.period_end,
            stage=str(r.stage),
            review_phase=r.review_phase,
            report_date=r.report_date,
            create_time=r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
        )
        for r in rows
    ]
    return ReviewSessionHistoryListOut(total=total, page=page, size=size, items=items)


@router.get("/crm/review/snapshot-filter-enums")
def query_review_snapshot_filter_enums(
    db_session: SessionDep,
    user: CurrentUserDep,
) -> ReviewSnapshotFilterEnumsOut:
    """
    筛选页用的枚举（分组维度、预测类型、商机阶段等）。登录即可调用。
    返回里的分组选项含人员/预测/阶段。
    """
    # 需要登录态；不额外限制角色（仅提供筛选枚举）
    _ = user

    forecast_rows = db_session.exec(
        select(CRMSystemConfiguration.config_key, CRMSystemConfiguration.config_value)
        .where(CRMSystemConfiguration.config_type == "ForecastTypeMapping")
        .order_by(CRMSystemConfiguration.config_key)
    ).all()
    forecast_types: list[str] = []
    for _config_key, config_value in forecast_rows:
        first = ""
        raw = str(config_value or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    first = str(parsed[0] or "").strip()
                else:
                    first = raw
            except Exception:
                first = raw
        if first:
            forecast_types.append(first)
    # 去重并保持顺序
    forecast_types = list(dict.fromkeys(forecast_types))

    stage_rows = db_session.exec(
        text(
            "select handbook_id, sales_stage "
            "from diagnostic_playbook "
            "where sales_stage is not null and sales_stage <> ''"
        )
    ).all()
    stage_by_handbook: dict[str, list[str]] = {}
    for r in stage_rows:
        hb = str(getattr(r, "handbook_id", "") or "").strip()
        stage = str(getattr(r, "sales_stage", "") or "").strip()
        if not hb or not stage:
            continue
        stage_by_handbook.setdefault(hb, [])
        if stage not in stage_by_handbook[hb]:
            stage_by_handbook[hb].append(stage)
    opportunity_stages = [
        {"handbook_id": hb, "sales_stages": stages}
        for hb, stages in sorted(stage_by_handbook.items(), key=lambda x: x[0])
    ]

    return ReviewSnapshotFilterEnumsOut.model_validate(
        {
            "group_by_options": [
                {"key": "owner", "label": "人员"},
                {"key": "forecast_type", "label": "预测类型"},
                {"key": "opportunity_stage", "label": "商机阶段"},
            ],
            "forecast_types": forecast_types,
            "opportunity_stages": opportunity_stages,
            "ai_forecast_types": ["NonCommit", "Commit"],
        }
    )


@router.post(
    "/crm/review/sessions/{session_id}/submit",
    response_model=ReviewBranchSnapshotSubmitOut,
)
def submit_my_review_branch_snapshot_changes(
    session_id: str,
    payload: ReviewBranchSnapshotSubmitIn,
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    提交本次 review 的商机快照修改（可一次提交多条）。仅在可编辑阶段成功；空数组也会记一次提交。
    请求体里每条只传允许改的字段，具体以 ``ReviewBranchSnapshotUpdateIn`` 为准。
    """
    return crm_review_service.submit_my_snapshot_changes(
        db_session,
        session_id=session_id,
        user_id=str(user.id),
        updates=[u.model_dump(exclude_unset=True) for u in (payload.updates or [])],
    )


@router.post(
    "/crm/review/sessions/{session_id}/branch-snapshots/merge-from-cache",
    response_model=ReviewBranchSnapshotMergeFromCacheOut,
)
def merge_branch_snapshots_from_cache_to_main(
    session_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    仅 session 负责人：cache 最初为主表镜像，销售在 cache 上改动的业务字段与 submit 白名单一致
    （预测类型、金额、商机阶段、预计成交日期），以及 submit 写入的修改元数据（如 ``update_time``、
    ``last_modified_by*``、``was_modified``、``modification_count``、``*_edit_modification_count``）
    按 ``opportunity_id`` + ``snapshot_period`` 写回主表；
    主表须已存在对应行（cache 仅为镜像上的修改）。若有 cache 行无主表行则跳过合并并打错误日志。每次调用在 ``crm_review_opp_audit_log`` 写一条审计
    （``change_scope``: ``leader_merge_cache_to_main``）。供主表再回写 CRM 等外部系统前调用。
    """
    data = crm_review_service.merge_branch_snapshots_from_cache_to_main(
        db_session,
        session_id=session_id,
        user_id=str(user.id),
    )
    return ReviewBranchSnapshotMergeFromCacheOut.model_validate(data)


@router.post("/crm/review/sessions/{session_id}/review-phase")
def update_review_session_phase(
    session_id: str,
    payload: ReviewSessionPhaseUpdateIn,
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    负责人在「负责人评审」阶段切换本次 review 是编辑中还是已关闭（edit / closed），可反复改。
    仅负责人可调；且仅当会话处于负责人评审阶段时允许，否则报错。
    """
    session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="review session not found")

    attendee = crm_review_attendee_repo.get_by_session_and_user_id(
        db_session, session_id=session_id, user_id=str(user.id)
    )
    if not attendee:
        raise HTTPException(status_code=403, detail="user is not attendee of this review session")
    if not bool(getattr(attendee, "is_leader", False)):
        raise HTTPException(status_code=403, detail="only session leader can update review_phase")

    if str(session.stage) != "lead_review":
        raise HTTPException(
            status_code=409,
            detail="review_phase can only be updated when session.stage is lead_review",
        )

    session.review_phase = payload.review_phase
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return {
        "session_id": str(session.unique_id),
        "stage": str(session.stage),
        "review_phase": str(session.review_phase or ""),
    }


@router.get("/crm/review/sessions/{session_id}/kpi-metrics")
def query_review_session_kpi_metrics(
    session_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
    scope_type: Optional[str] = None,
    calc_phase: Optional[str] = None,
) -> ReviewSessionKpiMetricsOut:
    """
    本次 review 的 KPI 指标列表，仅负责人可调。可用 ``scope_type``、``calc_phase`` 筛选。
    """
    session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="review session not found")

    attendee = crm_review_attendee_repo.get_by_session_and_user_id(
        db_session, session_id=session_id, user_id=str(user.id)
    )
    if not attendee:
        raise HTTPException(status_code=403, detail="user is not attendee of this review session")
    if not bool(getattr(attendee, "is_leader", False)):
        raise HTTPException(status_code=403, detail="only session leader can view kpi metrics")

    rows = crm_review_kpi_metrics_repo.list_by_session(
        db_session,
        session_id=session_id,
        scope_type=(scope_type or "").strip() or None,
        calc_phase=(calc_phase or "").strip() or None,
    )

    items: List[ReviewSessionKpiMetricOut] = []
    for r in rows:
        items.append(
            ReviewSessionKpiMetricOut(
                unique_id=str(r.unique_id),
                session_id=str(r.session_id),
                scope_type=str(r.scope_type),
                scope_id=r.scope_id,
                scope_name=r.scope_name,
                parent_scope_id=r.parent_scope_id,
                metric_category=str(r.metric_category),
                metric_name=str(r.metric_name),
                metric_value=float(r.metric_value) if r.metric_value is not None else None,
                metric_value_prev=float(r.metric_value_prev) if r.metric_value_prev is not None else None,
                metric_delta=float(r.metric_delta) if r.metric_delta is not None else None,
                metric_rate=float(r.metric_rate) if r.metric_rate is not None else None,
                metric_unit=r.metric_unit,
                metric_content=r.metric_content,
                metric_content_en=r.metric_content_en,
                calc_phase=r.calc_phase,
                period_type=r.period_type,
                period=r.period,
                report_date=r.report_date,
                report_year=r.report_year,
                report_week_of_year=r.report_week_of_year,
            )
        )

    return ReviewSessionKpiMetricsOut(
        session_id=session_id,
        total=len(items),
        items=items,
    )


@router.post("/crm/review/sessions/{session_id}/insights")
def query_review_session_insights(
    session_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
    fields_level: Literal["basic", "full"] = "basic",
) -> Union[ReviewSessionInsightsBasicOut, ReviewSessionInsightsOut]:
    """
    部门视角的风险与进展洞察，仅负责人可调。风险为列表，进展按类别分组。
    查询参数 ``fields_level``：basic（默认）或 full，字段多少不同。
    """
    session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="review session not found")

    attendee = crm_review_attendee_repo.get_by_session_and_user_id(
        db_session, session_id=session_id, user_id=str(user.id)
    )
    if not attendee:
        raise HTTPException(status_code=403, detail="user is not attendee of this review session")
    if not bool(getattr(attendee, "is_leader", False)):
        raise HTTPException(status_code=403, detail="only session leader can view insights")

    rows = db_session.exec(
        select(CRMReviewOppRiskProgress)
        .where(
            CRMReviewOppRiskProgress.session_id == session_id,
            CRMReviewOppRiskProgress.scope_type == "department",
        )
        .order_by(
            CRMReviewOppRiskProgress.updated_at.desc(),
            CRMReviewOppRiskProgress.detected_at.desc(),
            CRMReviewOppRiskProgress.id.desc(),
        )
    ).all()

    is_full = str(fields_level or "basic").strip().lower() == "full"
    if is_full:
        risk_items: List[ReviewSessionInsightItemOut] = []
        progress_by_category: dict[str, list[ReviewSessionInsightItemOut]] = {}
    else:
        risk_items: List[ReviewSessionRiskInsightItemBasicOut] = []
        progress_by_category: dict[str, list[ReviewSessionInsightItemBaseOut]] = {}
    progress_total = 0

    for row in rows:
        record_type = str(getattr(row, "record_type", "") or "").upper()
        if is_full:
            item = ReviewSessionInsightItemOut(
                insight_unique_id=str(row.unique_id),
                type_code=str(row.type_code),
                type_name=str(row.type_name),
                record_type=str(row.record_type),
                judgment_rule=row.judgment_rule,
                summary=row.summary,
                severity=row.severity,
                source=row.source,
                metric_name=row.metric_name,
                category=row.category,
                gap_description=row.gap_description,
                detail_description=row.detail_description,
                solution=row.solution,
                status=row.status,
                detected_at=row.detected_at,
                updated_at=row.updated_at,
            )
        else:
            if record_type == "RISK":
                item = ReviewSessionRiskInsightItemBasicOut(
                    insight_unique_id=str(row.unique_id),
                    type_code=str(row.type_code),
                    type_name=str(row.type_name),
                    record_type=str(row.record_type),
                    judgment_rule=row.judgment_rule,
                    summary=row.summary,
                    category=row.category,
                    gap_description=row.gap_description,
                    detail_description=row.detail_description,
                )
            else:
                item = ReviewSessionInsightItemBaseOut(
                    insight_unique_id=str(row.unique_id),
                    type_code=str(row.type_code),
                    type_name=str(row.type_name),
                    record_type=str(row.record_type),
                    summary=row.summary,
                )
        if record_type == "RISK":
            risk_items.append(item)
        elif record_type == "PROGRESS":
            category = str(getattr(row, "category", "") or "").strip()
            progress_by_category.setdefault(category, []).append(item)
            progress_total += 1

    if is_full:
        progress_items: List[ReviewSessionProgressCategoryGroupOut] = [
            ReviewSessionProgressCategoryGroupOut(
                category=category,
                items=items,
            )
            for category, items in progress_by_category.items()
        ]
        return ReviewSessionInsightsOut(
            session_id=session_id,
            scope_type="department",
            risk_total=len(risk_items),
            progress_total=progress_total,
            risk_items=risk_items,
            progress_items=progress_items,
        )
    progress_items_basic: List[ReviewSessionProgressCategoryGroupBasicOut] = [
        ReviewSessionProgressCategoryGroupBasicOut(
            category=category,
            items=items,
        )
        for category, items in progress_by_category.items()
    ]
    return ReviewSessionInsightsBasicOut(
        session_id=session_id,
        scope_type="department",
        risk_total=len(risk_items),
        progress_total=progress_total,
        risk_items=risk_items,
        progress_items=progress_items_basic,
    )


@router.post(
    "/crm/review/sessions/{session_id}/insights/risk/{risk_id}/opportunities",
    response_model_exclude_none=True,
)
def query_review_session_insight_risk_opportunities(
    session_id: str,
    risk_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
) -> ReviewSessionInsightDetailOut:
    """
    某条风险洞察关联的商机列表，仅负责人可调。``risk_id`` 与 insights 里风险项的 ``insight_unique_id`` 一致；响应含洞察摘要与关联商机。
    """
    session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="review session not found")

    attendee = crm_review_attendee_repo.get_by_session_and_user_id(
        db_session, session_id=session_id, user_id=str(user.id)
    )
    if not attendee:
        raise HTTPException(status_code=403, detail="user is not attendee of this review session")
    if not bool(getattr(attendee, "is_leader", False)):
        raise HTTPException(status_code=403, detail="only session leader can view insights")
    risk_id = str(risk_id or "").strip()
    if not risk_id:
        raise HTTPException(status_code=422, detail="risk_id is required")

    insight = db_session.exec(
        select(CRMReviewOppRiskProgress).where(
            CRMReviewOppRiskProgress.session_id == session_id,
            CRMReviewOppRiskProgress.scope_type == "department",
            CRMReviewOppRiskProgress.record_type == "RISK",
            CRMReviewOppRiskProgress.unique_id == risk_id,
        )
    ).first()
    if not insight:
        raise HTTPException(status_code=404, detail="insight not found")
    detail_description = insight.detail_description

    opportunities: List[ReviewSessionInsightRiskOpportunityOut] = []
    rel_rows = db_session.exec(
        select(CRMReviewRiskOpportunityRelation)
        .where(
            CRMReviewRiskOpportunityRelation.session_id == session_id,
            CRMReviewRiskOpportunityRelation.risk_unique_id == risk_id,
        )
        .order_by(
            CRMReviewRiskOpportunityRelation.updated_at.desc(),
        )
    ).all()

    for rel in rel_rows:
        opportunities.append(
            ReviewSessionInsightRiskOpportunityOut(
                relation_unique_id=str(rel.unique_id),
                risk_unique_id=str(rel.risk_unique_id),
                opportunity_id=str(rel.opportunity_id),
                owner_id=rel.owner_id,
                department_id=rel.department_id,
                snapshot_period=str(rel.snapshot_period),
                calc_phase=str(rel.calc_phase),
                relation_reason=rel.relation_reason,
                relation_rank=rel.relation_rank,
                relation_weight=float(rel.relation_weight)
                if rel.relation_weight is not None
                else None,
            )
        )

    return ReviewSessionInsightDetailOut(
        insight_unique_id=str(insight.unique_id),
        session_id=session_id,
        scope_type="department",
        record_type="RISK",
        type_code=str(insight.type_code),
        type_name=str(insight.type_name),
        category=insight.category,
        judgment_rule=insight.judgment_rule,
        summary=insight.summary,
        gap_description=insight.gap_description,
        detail_description=detail_description,
        opportunities=opportunities,
        severity=insight.severity,
        source=insight.source,
        metric_name=insight.metric_name,
        solution=insight.solution,
        status=insight.status,
        detected_at=insight.detected_at,
        updated_at=insight.updated_at,
    )


@router.post("/crm/review/sessions/{session_id}/recalculate-forecast-aggregates")
def recalculate_review_session_forecast_aggregates(
    session_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
) -> ReviewSessionForecastRecalcOut:
    """
    触发本次 review 的预测/业绩聚合重算（结果来自外部服务）。参会人均可调用：负责人拉全场，普通成员只拉本人。
    具体字段见响应模型。
    """
    data = crm_review_service.recalculate_forecast_aggregates(
        db_session,
        session_id=session_id,
        user_id=str(user.id),
    )
    return ReviewSessionForecastRecalcOut.model_validate(data)


class ReviewSessionChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., min_length=1)
    chat_engine: str = "default"
    chat_id: Optional[UUID] = None
    stream: bool = True
    preset_template: Optional[str] = None
    template_params: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("messages")
    @classmethod
    def check_messages(cls, messages: List[ChatMessage]) -> List[ChatMessage]:
        if not messages:
            raise ValueError("messages cannot be empty")
        for m in messages:
            if m.role not in [MessageRole.USER, MessageRole.ASSISTANT]:
                raise ValueError("role must be either 'user' or 'assistant'")
            if not m.content:
                raise ValueError("message content cannot be empty")
        if messages[-1].role != MessageRole.USER:
            raise ValueError("last message must be from user")
        return messages


@router.post("/crm/review/sessions/{session_id}/chat")
def review_session_chat(
    request: Request,
    session_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
    chat_request: ReviewSessionChatRequest,
):
    """
    Review session Q&A endpoint. Supports three types of questions:
    - data_query: factual lookups (what)
    - root_cause: why a metric changed (why)
    - strategy: actionable recommendations (how)

    Only session attendees can access.
    """
    session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    attendee = crm_review_attendee_repo.get_by_session_and_user_id(
        db_session, session_id=session_id, user_id=str(user.id)
    )
    # Superuser can bypass attendee restriction for emergency support/ops usage.
    if not attendee and not bool(getattr(user, "is_superuser", False)):
        raise HTTPException(
            status_code=403,
            detail="User is not an attendee of this review session",
        )

    is_attendee_leader = bool(getattr(attendee, "is_leader", False)) if attendee else False
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    browser_id = getattr(request.state, "browser_id", "")

    context: Dict[str, Any] = {"review_session_id": session_id}
    if attendee and not is_attendee_leader:
        owner_id = str(getattr(attendee, "crm_user_id", "") or "").strip()
        if not owner_id:
            raise HTTPException(status_code=422, detail="attendee has no crm_user_id")
        context["enforced_owner_id"] = owner_id
    if chat_request.preset_template is not None:
        context["preset_template"] = chat_request.preset_template
    if isinstance(chat_request.template_params, dict) and chat_request.template_params:
        context["template_params"] = chat_request.template_params

    chat_flow = ChatFlow(
        db_session=db_session,
        user=user,
        browser_id=browser_id,
        origin=origin,
        chat_id=chat_request.chat_id,
        chat_messages=chat_request.messages,
        engine_name=chat_request.chat_engine,
        chat_type=ChatType.REVIEW_SESSION,
        context=context,
    )

    if chat_request.stream:
        return StreamingResponse(
            chat_flow.chat(),
            media_type="text/event-stream",
        )
    return get_final_chat_result(chat_flow.chat())


@router.post("/crm/review/sessions/{session_id}/build-index")
def build_review_session_index(
    session_id: str,
    db_session: SessionDep,
    user: OptionalUserDep,
    kb_id: Optional[int] = None,
    review_data_types: Optional[List[CrmDataType]] = Query(
        default=None,
        description=(
            "可选：仅构建指定 review 数据类型（默认构建全部三种）。"
            "可多选：crm_review_session, crm_review_snapshot, crm_review_risk_progress"
        ),
    ),
):
    """手动触发某个 review session 的向量 + 知识图谱索引构建。

    默认会构建该 session 下三种 review 数据：
    ``crm_review_session``、``crm_review_snapshot``、``crm_review_risk_progress``，
    生成 Document 并异步执行向量 embedding 和 CRM 知识图谱构建。

    支持部分构建：通过 ``review_data_types`` 仅构建指定类型，
    例如只构建 ``crm_review_risk_progress``。

    Parameters
    ----------
    kb_id : int | None
        目标知识库 ID。不传则使用 review 专用知识库 (CRM_REVIEW_KB_ID)。
    """
    session_obj = crm_review_session_repo.get_by_unique_id(db_session, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Review session not found")

    from app.rag.chat.review.indexing_policy import (
        normalize_review_data_types,
        validate_review_index_scope_by_stage,
    )
    from app.tasks.review_index import index_review_session_data

    selected_types = normalize_review_data_types(
        [t.value for t in (review_data_types or [])] or None
    )
    try:
        validate_review_index_scope_by_stage(session_obj.stage, selected_types)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    index_review_session_data.delay(
        session_id,
        kb_id=kb_id,
        review_data_types=selected_types,
    )
    logger.info(
        "User %s triggered review index build for session %s, kb_id=%s, types=%s",
        user.id if user else "system",
        session_id,
        kb_id,
        selected_types,
    )

    return {
        "detail": f"Review session index build triggered for session {session_id}",
        "session_id": session_id,
        "kb_id": kb_id,
        "review_data_types": selected_types,
    }
