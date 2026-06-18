import logging
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.api.deps import SessionDep
from app.api.routes.notification_schemas import (
    DailyNoFollowupReminderPushRequest,
    PushNotificationRequest,
    ReviewSessionPushRequest,
    SalesTaskCreatedPushRequest,
    VisitRecordCardPushRequest,
    VisitRecordCommentPushRequest,
    WeeklyFollowupCommentPushRequest,
)
from app.core.config import settings
from app.repositories.user_profile import user_profile_repo
from app.utils.date_utils import beijing_today_date
from app.crm.save_engine import (
    _crm_visit_record_row_to_push_dict,
    push_visit_record_message,
    report_visit_record_billing,
)
from app.models.crm_review import CRMReviewAttendee
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.repositories.document_content import DocumentContentRepo
from app.services.visit_record_card_push_status import (
    VisitRecordCardPushStatus,
    get_visit_record_card_push_status,
    update_visit_record_card_push_status,
)
from app.services.visit_task_eval import tasks_to_card_payload

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/notification"
)

# ---------- 团队 review 流程事件类型（与 CRMReviewSession.stage 一致）----------
# stage 与 CRMReviewSession.stage 一致。接收人由 CRMReviewAttendee 框定。
# initial_edit → 该 session 全员推送（开启第一轮数据修改窗口期）；lead_review → 该 session 中 is_leader=True 推送（查看报告，开启第二轮数据修改窗口期：review_phase=edit）
EVENT_REVIEW_INITIAL_EDIT = "initial_edit"    # 全员
EVENT_REVIEW_LEAD_REVIEW = "lead_review"      # 团队 leader

REVIEW_STAGE_CONFIG: Dict[str, Dict[str, str]] = {
    EVENT_REVIEW_INITIAL_EDIT: {
        "recipient_scope": "all",
    },
    EVENT_REVIEW_LEAD_REVIEW: {
        "recipient_scope": "leader_only",
    },
}


def _normalize_recipient_user_ids(user_ids: Optional[List[str]]) -> List[str]:
    """去空、去重并保持原顺序。"""
    raw_ids: List[str] = []
    if user_ids:
        raw_ids.extend([str(x) for x in user_ids if x is not None])

    result: List[str] = []
    seen: set[str] = set()
    for rid in raw_ids:
        rid = str(rid or "").strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        result.append(rid)
    return result


def _resolve_review_recipients_by_stage(
    db_session: SessionDep,
    session_id: str,
    stage: str,
) -> List[str]:
    """
    根据 stage、session_id 确定接收人。
    接收人来自 CRMReviewAttendee：initial_edit 全员；lead_review 仅 is_leader=True。
    """
    cfg = REVIEW_STAGE_CONFIG.get(stage)
    if not cfg:
        raise HTTPException(status_code=422, detail=f"unsupported review stage: {stage}")

    # 接收人由 CRMReviewAttendee 框定：按 stage 取全员或仅 leader
    is_leader_only = cfg.get("recipient_scope") == "leader_only"
    stmt = select(CRMReviewAttendee.user_id).where(CRMReviewAttendee.session_id == session_id)
    if is_leader_only:
        stmt = stmt.where(CRMReviewAttendee.is_leader == True)  # noqa: E712
    rows = db_session.exec(stmt).all()
    recipient_ids = list({str(uid) for uid in rows if uid})

    if not recipient_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No recipients in CRMReviewAttendee for session_id={session_id}"
                + (" (is_leader=True)" if is_leader_only else " (全员)")
            ),
        )
    return recipient_ids


def _build_review_session_jump_url(session_id: str) -> str:
    base_url = (getattr(settings, "REVIEW_REPORT_HOST", None) or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=500,
            detail="REVIEW_REPORT_HOST not configured; cannot resolve review page URL",
        )
    return f"{base_url}{getattr(settings, 'REVIEW_SESSION_PAGE_URL', '')}?sessionId={session_id}"


def _append_query_params(url: str, **params: str) -> str:
    """Append/override query params in URL."""
    if not url:
        return ""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if v is not None})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _build_comment_notification_message(
    *,
    author_name: Optional[str],
    title: str,
    label: str,
    link_text: Optional[str],
    jump_url: Optional[str],
    content: Optional[str],
) -> str:
    """评论类文本消息：{author}{title}\\n[{link_text}]({jump_url})\\n{label}：{content}"""
    author = (author_name or "").strip() or "有人"
    link_display = (link_text or "").strip() or "查看详情"
    url = (jump_url or "").strip()
    link_line = f"[{link_display}]({url})" if url else ((link_text or "").strip() or "")
    content_preview = (content or "").strip()
    if len(content_preview) > 200:
        content_preview = content_preview[:197] + "..."
    content_preview = content_preview or "--"

    message_text = f"{author}{title}\n"
    if link_line:
        message_text += f"{link_line}\n"
    message_text += f"{label}：{content_preview}\n"
    return message_text


def _build_sales_task_created_message(payload: SalesTaskCreatedPushRequest) -> str:
    """
    销售任务创建推送文案：
    {创建人}在 {创建时间} 帮你创建了{N}个任务：
    【{客户/商机}】   （link_text，可选）
    [{任务详情}]({jump_url})
    """
    creator = (payload.author_name or "").strip() or "有人"
    created_at = (payload.created_at or "").strip() or "--"
    task_count = payload.task_count

    link_text = (payload.link_text or "").strip()
    content = (payload.content or "").strip() or "--"

    jump_url = (payload.jump_url or "").strip()
    if not jump_url:
        base_url = (settings.CRM_SALES_TASK_PAGE_URL or "").strip().rstrip("/")
        task_id = payload.task_id.strip()
        jump_url = f"{base_url}/{quote(task_id, safe='')}" if base_url and task_id else (base_url or "")

    lines: List[str] = [f"{creator}在{created_at}帮你创建了{task_count}个任务："]
    if link_text:
        lines.append(f"【{link_text}】")
    content_line = f"[{content}]({jump_url})" if jump_url else content
    lines.append(content_line)

    return "\n".join(lines) + "\n"


def _resolve_visit_record_card_tasks(
    visit_tasks: Optional[List[Dict[str, Any]]],
) -> tuple[List[Dict[str, Any]], int]:
    """由 Aldebaran 传入的 visit_tasks 归一化任务列表，task_count 为列表长度。"""
    eval_result = tasks_to_card_payload(visit_tasks or [])
    return eval_result.tasks, eval_result.task_count


def _load_document_content_for_visit_record_card(
    db_session: SessionDep,
    record_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """从 document_contents 读取会议纪要总结与风险信息（link 类型拜访）。"""
    try:
        repo = DocumentContentRepo()
        doc = repo.get_by_visit_record_id(db_session, record_id)
        if not doc:
            return None, None
        meeting_notes = (doc.meeting_summary or "").strip() or None
        risk_info = (doc.risk_info or "").strip() or None
        return meeting_notes, risk_info
    except Exception as exc:
        logger.warning(
            "Failed to load document_content for visit record card, record_id=%s: %s",
            record_id,
            exc,
        )
    return None, None


def _handle_visit_record_card_push(
    db_session: SessionDep,
    payload: VisitRecordCardPushRequest,
) -> Dict[str, Any]:
    record_id = payload.record_id.strip()
    if not record_id:
        raise HTTPException(status_code=422, detail="visit_record_card requires record_id")

    row = db_session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"visit record not found: {record_id}")

    current_status = get_visit_record_card_push_status(db_session, record_id)
    is_revised = bool(getattr(payload, "is_revised", False))
    revision_seq = getattr(payload, "revision_seq", None)
    if current_status == VisitRecordCardPushStatus.PUSHED and not is_revised:
        logger.info(
            "Visit record card already pushed, skip duplicate callback, record_id=%s",
            record_id,
        )
        return {
            "success": True,
            "record_id": record_id,
            "skipped": True,
            "task_count": 0,
            "recipients_count": 0,
            "success_count": 0,
            "failed_recipients": [],
        }

    if is_revised and revision_seq is None:
        revision_seq = int(getattr(row, "revision_count", 0) or 0) or None

    visit_type = (row.visit_type or "form").strip() or "form"
    meeting_notes: Optional[str] = None
    risk_info: Optional[str] = None
    if visit_type == "link":
        meeting_notes, risk_info = _load_document_content_for_visit_record_card(
            db_session, record_id
        )

    tasks, task_count = _resolve_visit_record_card_tasks(payload.visit_tasks)
    sales_visit_record = _crm_visit_record_row_to_push_dict(row)

    push_ok = push_visit_record_message(
        record_id=record_id,
        sales_visit_record=sales_visit_record,
        visit_type=visit_type,
        db_session=db_session,
        meeting_notes=meeting_notes,
        risk_info=risk_info,
        saved_time=row.last_modified_time,
        tasks=tasks,
        task_count=task_count,
        is_revised=is_revised,
    )

    push_status = (
        VisitRecordCardPushStatus.PUSHED
        if push_ok
        else VisitRecordCardPushStatus.FAILED
    )
    update_visit_record_card_push_status(
        db_session, record_id, push_status, commit=True
    )
    if is_revised and revision_seq is not None:
        from app.repositories.visit_record_revisions import visit_record_revisions_repo

        visit_record_revisions_repo.update_card_push_status(
            db_session,
            record_id=record_id,
            revision_seq=int(revision_seq),
            card_push_status=push_status,
            commit=True,
        )

    operator_user_id = str(row.recorder_id) if row.recorder_id else None
    if push_ok and operator_user_id:
        try:
            report_visit_record_billing(UUID(str(operator_user_id)), record_id)
        except Exception as exc:
            logger.error(
                "Visit record billing after card push failed, record_id=%s: %s",
                record_id,
                exc,
                exc_info=True,
            )

    return {
        "success": push_ok,
        "record_id": record_id,
        "card_push_status": push_status,
        "task_count": task_count,
        "recipients_count": 0,
        "success_count": 1 if push_ok else 0,
        "failed_recipients": [] if push_ok else [{"message": "visit record card push failed"}],
    }


def _resolve_daily_no_followup_check_date(check_date: Optional[str]) -> date:
    """解析检查日期，默认北京时间当天。"""
    raw = (check_date or "").strip()
    if not raw:
        return beijing_today_date()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="daily_no_followup_reminder check_date must be YYYY-MM-DD",
        ) from exc


def _build_daily_no_followup_reminder_jump_url() -> str:
    host = (settings.REVIEW_REPORT_HOST or "").strip().rstrip("/")
    path = (settings.CRM_VISIT_FOLLOWUP_ENTRY_PAGE_URL or "").strip()
    if not host or not path:
        return ""
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{host}{path}"


def _build_daily_no_followup_reminder_message(jump_url: str) -> str:
    link_line = f"[立即录入跟进]({jump_url})" if jump_url else "立即录入跟进"
    return (
        "今天还没有客户跟进或任务进展\n"
        "如果今日已完成客户沟通，建议及时补充跟进记录，系统会自动识别任务进展并更新状态\n"
        f"{link_line}\n"
    )


def _resolve_daily_no_followup_reminder_recipients(
    db_session: SessionDep,
    recipient_user_ids: Optional[List[str]],
) -> List[str]:
    sales_profiles = user_profile_repo.get_active_sales_users_with_oauth(db_session)
    sales_user_ids = {str(p.user_id) for p in sales_profiles if p.user_id}

    explicit = _normalize_recipient_user_ids(recipient_user_ids)
    if explicit:
        return [uid for uid in explicit if uid in sales_user_ids]
    return sorted(sales_user_ids)


def _recorder_ids_with_visit_on_date(
    db_session: SessionDep,
    check_date: date,
    candidate_user_ids: List[str],
) -> Set[str]:
    """返回在 check_date 当天已有拜访记录（visit_communication_date）的销售 user_id 集合。"""
    if not candidate_user_ids:
        return set()

    recorder_uuids: List[UUID] = []
    for uid in candidate_user_ids:
        try:
            recorder_uuids.append(UUID(str(uid)))
        except Exception:
            continue
    if not recorder_uuids:
        return set()

    rows = db_session.exec(
        select(CRMSalesVisitRecord.recorder_id).where(
            CRMSalesVisitRecord.visit_communication_date == check_date,
            CRMSalesVisitRecord.recorder_id.in_(recorder_uuids),
        )
    ).all()
    return {str(rid) for rid in rows if rid}


def _handle_daily_no_followup_reminder_push(
    db_session: SessionDep,
    payload: DailyNoFollowupReminderPushRequest,
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    check_date = _resolve_daily_no_followup_check_date(payload.check_date)
    candidate_ids = _resolve_daily_no_followup_reminder_recipients(
        db_session, payload.recipient_user_ids
    )
    if not candidate_ids:
        raise HTTPException(
            status_code=422,
            detail="daily_no_followup_reminder requires recipient_user_ids or active sales users (role=sales)",
        )

    with_records = _recorder_ids_with_visit_on_date(db_session, check_date, candidate_ids)
    recipient_ids = [uid for uid in candidate_ids if uid not in with_records]
    skipped_with_records = [uid for uid in candidate_ids if uid in with_records]

    if not recipient_ids:
        return {
            "success": True,
            "check_date": check_date.isoformat(),
            "recipients_count": 0,
            "success_count": 0,
            "skipped_with_records_count": len(skipped_with_records),
            "failed_recipients": [],
        }

    message_text = _build_daily_no_followup_reminder_message(
        _build_daily_no_followup_reminder_jump_url()
    )
    send_fn = platform_notification_service.send_daily_no_followup_reminder_notification

    batch_result = _dispatch_text_notification_batch(
        db_session,
        recipient_ids=recipient_ids,
        message_text=message_text,
        send_fn=send_fn,
    )

    return {
        "success": batch_result["success"],
        "check_date": check_date.isoformat(),
        "recipients_count": batch_result["recipients_count"],
        "success_count": batch_result["success_count"],
        "skipped_with_records_count": len(skipped_with_records),
        "failed_recipients": batch_result["failed_recipients"],
    }


def _build_review_session_message(stage: str, session_id: str) -> str:
    """按 stage 生成 review_session 消息模板。"""
    jump_url = _build_review_session_jump_url(session_id)
    jump_url_initial = _append_query_params(jump_url, agent="evaluate")
    jump_url_lead = _append_query_params(jump_url, agent="insight")
    link_initial = f"[点击查看并更新]({jump_url_initial})" if jump_url_initial else "点击查看并更新"
    link_lead = f"[点击查看完整报告]({jump_url_lead})" if jump_url_lead else "点击查看完整报告"

    if stage == EVENT_REVIEW_INITIAL_EDIT:
        return (
            "<b>本周个人经营决策分析报告已更新</b>\n"
            "你的本周业务进展与商机分析已经生成，请尽快完成以下更新：\n"
            "- 核对并更新当前商机阶段\n"
            "- 补充或修正预计签约时间\n"
            "- 完善下一步推进计划\n"
            f"{link_initial}\n"
            "本次更新将直接影响本周团队经营决策分析的预测结果与讨论内容，请优先完成。"
        )

    return (
        "<b>本周团队经营决策分析已准备完成</b>\n"
        "请提前查看，为会议做好决策准备：\n"
        f"{link_lead}\n"
        "建议在会议前完成浏览和信息补充，便于现场快速决策与推进。"
    )


def _dispatch_text_notification_batch(
    db_session: SessionDep,
    *,
    recipient_ids: List[str],
    message_text: str,
    send_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    success_count = 0
    failed: List[dict] = []
    for rid in recipient_ids:
        try:
            r = send_fn(db_session, recipient_user_id=rid, message_text=message_text)
        except Exception as exc:
            logger.warning(
                "Text notification failed for recipient=%s: %s",
                rid,
                exc,
                exc_info=True,
            )
            failed.append({"recipient_user_id": rid, "message": str(exc)})
            continue
        if r.get("success"):
            success_count += 1
        else:
            failed.append({"recipient_user_id": rid, "message": r.get("message")})
    return {
        "success": success_count > 0,
        "recipients_count": len(recipient_ids),
        "success_count": success_count,
        "failed_recipients": failed,
    }


def _handle_review_session_push(
    db_session: SessionDep,
    payload: ReviewSessionPushRequest,
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    stage = payload.context.stage.strip()
    session_id = payload.context.session_id.strip()
    if not stage:
        raise HTTPException(status_code=422, detail="review_session requires context.stage")
    if not session_id:
        raise HTTPException(status_code=422, detail="review_session requires context.session_id")

    if stage not in REVIEW_STAGE_CONFIG:
        logger.info("Review job: stage=%s does not trigger notification", stage)
        return {
            "success": False,
            "recipients_count": 0,
            "success_count": 0,
            "failed_recipients": [],
        }

    recipient_ids = _normalize_recipient_user_ids(
        _resolve_review_recipients_by_stage(db_session, session_id, stage)
    )
    if not recipient_ids:
        raise HTTPException(status_code=422, detail="no review session recipients resolved")

    message_text = _build_review_session_message(stage, session_id)
    if not settings.CRM_REVIEW_SESSION_NOTIFICATION_ENABLED:
        logger.info(
            "Review notification recorded only: stage=%s session_id=%s recipients=%s message=%s",
            stage,
            session_id,
            recipient_ids,
            message_text,
        )
        return {
            "success": True,
            "recorded_only": True,
            "recipients_count": len(recipient_ids),
            "success_count": 0,
            "failed_recipients": [],
        }

    return _dispatch_text_notification_batch(
        db_session,
        recipient_ids=recipient_ids,
        message_text=message_text,
        send_fn=platform_notification_service.send_review_session_notification,
    )


def _handle_weekly_followup_comment_push(
    db_session: SessionDep,
    payload: WeeklyFollowupCommentPushRequest,
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    recipient_ids = _normalize_recipient_user_ids(payload.recipient_user_ids)
    if not recipient_ids:
        raise HTTPException(status_code=422, detail="recipient_user_ids is required")
    message_text = _build_comment_notification_message(
        author_name=payload.author_name,
        title="评论了你的周跟进总结",
        label="评论",
        link_text=payload.link_text,
        jump_url=payload.jump_url,
        content=payload.content,
    )
    return _dispatch_text_notification_batch(
        db_session,
        recipient_ids=recipient_ids,
        message_text=message_text,
        send_fn=platform_notification_service.send_weekly_followup_comment_notification,
    )


def _handle_visit_record_comment_push(
    db_session: SessionDep,
    payload: VisitRecordCommentPushRequest,
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    recipient_ids = _normalize_recipient_user_ids(payload.recipient_user_ids)
    if not recipient_ids:
        raise HTTPException(status_code=422, detail="recipient_user_ids is required")
    message_text = _build_comment_notification_message(
        author_name=payload.author_name,
        title="评论了你的拜访记录",
        label="评论",
        link_text=payload.link_text,
        jump_url=payload.jump_url,
        content=payload.content,
    )
    return _dispatch_text_notification_batch(
        db_session,
        recipient_ids=recipient_ids,
        message_text=message_text,
        send_fn=platform_notification_service.send_visit_record_comment_notification,
    )


def _handle_sales_task_created_push(
    db_session: SessionDep,
    payload: SalesTaskCreatedPushRequest,
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    recipient_ids = _normalize_recipient_user_ids(payload.recipient_user_ids)
    if not recipient_ids:
        raise HTTPException(status_code=422, detail="recipient_user_ids is required")
    return _dispatch_text_notification_batch(
        db_session,
        recipient_ids=recipient_ids,
        message_text=_build_sales_task_created_message(payload),
        send_fn=platform_notification_service.send_sales_task_created_notification,
    )


@router.post("/push")
async def push_notification_api(
    payload: PushNotificationRequest,
    db_session: SessionDep,
):
    """
    统一消息推送入口（请求体按 type 判别，字段见 notification_schemas）：
    weekly_followup_comment / visit_record_comment / sales_task_created /
    review_session / visit_record_card / daily_no_followup_reminder
    """
    try:
        if isinstance(payload, VisitRecordCardPushRequest):
            result = _handle_visit_record_card_push(db_session, payload)
        elif isinstance(payload, DailyNoFollowupReminderPushRequest):
            result = _handle_daily_no_followup_reminder_push(db_session, payload)
        elif isinstance(payload, ReviewSessionPushRequest):
            result = _handle_review_session_push(db_session, payload)
        elif isinstance(payload, WeeklyFollowupCommentPushRequest):
            result = _handle_weekly_followup_comment_push(db_session, payload)
        elif isinstance(payload, VisitRecordCommentPushRequest):
            result = _handle_visit_record_comment_push(db_session, payload)
        elif isinstance(payload, SalesTaskCreatedPushRequest):
            result = _handle_sales_task_created_push(db_session, payload)
        else:
            raise HTTPException(status_code=422, detail="unsupported notification type")

        return {"code": 200, "message": "ok", "result": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to push notification: {e}")
        raise HTTPException(status_code=500, detail="Failed to push notification")