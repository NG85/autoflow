import logging
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import SessionDep
from app.core.config import settings
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


class PushNotificationRequest(BaseModel):
    """
    统一推送接口请求体：
    - weekly_followup_comment: 周跟进总结评论提醒（文本消息）
    - visit_record_comment: 拜访记录评论提醒（文本消息）
    - sales_task_created: 外部服务创建销售任务后推送（文本消息）
      必传 task_id、author_name（创建人）、created_at（创建时间）、content（任务详情，含截止时间）；
      link_text 为客户/商机文案（可选）；超链接仅包在 content 上；
      未传 jump_url 时兜底 CRM_SALES_TASK_PAGE_URL/{task_id}（路径拼接）
    - review_session: review 阶段推进触发的推送（需要调用方传 context.stage/context.session_id）
    - visit_record_card: Aldebaran 拜访后处理完成后推送拜访卡片（仅传 record_id、visit_tasks；
      拜访记录与 visit_type 由库表查询，link 类型再查 document_contents 的 meeting_notes/risk_info，task_count 由 visit_tasks 长度得出）
    """

    type: Literal[
        "weekly_followup_comment",
        "visit_record_comment",
        "sales_task_created",
        "review_session",
        "visit_record_card",
    ]
    context: Optional[Dict[str, Any]] = None

    # visit_record_card：拜访记录业务主键
    record_id: Optional[str] = None

    # 接收人（visit_record_card 由推送服务按记录人/上级等自动解析，可不传）
    recipient_user_ids: Optional[List[str]] = None

    # 消息作者（可选）
    author_name: Optional[str] = None

    # 跳转链接与展示文本（链接可选；展示文本可选）
    jump_url: Optional[str] = None
    link_text: Optional[str] = None

    # 内容摘要（评论内容 / 任务标题等，允许为空）
    content: Optional[str] = None

    # sales_task_created 专用
    created_at: Optional[str] = None
    task_count: Optional[int] = 1
    task_id: Optional[str] = None

    # visit_record_card：状态变更任务列表（Aldebaran 回调）
    visit_tasks: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description=(
            "拜访卡片 tasks 变量，每项含 task_status、task_title、task_id；"
            "id 由服务端按数组顺序生成；未传或空则 task_count=0"
        ),
    )


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


def _build_sales_task_created_message(payload: PushNotificationRequest) -> str:
    """
    销售任务创建推送文案：
    {创建人}在 {创建时间} 帮你创建了{N}个任务：
    【{客户/商机}】   （link_text，可选）
    [{任务详情}]({jump_url})
    """
    creator = (payload.author_name or "").strip() or "有人"
    created_at = (payload.created_at or "").strip() or "--"
    task_count = payload.task_count if payload.task_count and payload.task_count > 0 else 1

    link_text = (payload.link_text or "").strip()
    content = (payload.content or "").strip() or "--"

    jump_url = (payload.jump_url or "").strip()
    if not jump_url:
        base_url = (settings.CRM_SALES_TASK_PAGE_URL or "").strip().rstrip("/")
        task_id = (payload.task_id or "").strip()
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
    payload: PushNotificationRequest,
) -> Dict[str, Any]:
    record_id = (payload.record_id or "").strip()
    if not record_id:
        raise HTTPException(status_code=422, detail="visit_record_card requires record_id")

    row = db_session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"visit record not found: {record_id}")

    current_status = get_visit_record_card_push_status(db_session, record_id)
    if current_status == VisitRecordCardPushStatus.PUSHED:
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
    )

    push_status = (
        VisitRecordCardPushStatus.PUSHED
        if push_ok
        else VisitRecordCardPushStatus.FAILED
    )
    update_visit_record_card_push_status(
        db_session, record_id, push_status, commit=True
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


@router.post("/push")
async def push_notification_api(
    payload: PushNotificationRequest,
    db_session: SessionDep,
):
    """
    统一消息推送入口：
    1) 周总结保存 comments 后给负责人推送（weekly_followup_comment）
    2) 拜访记录保存 comments 后给跟进人推送（visit_record_comment）
    3) 外部服务创建销售任务后推送（sales_task_created）
    4) review session 阶段推进触发推送（review_session，调用方传 context.stage/context.session_id）
    5) Aldebaran 拜访后处理完成后推送拜访卡片（visit_record_card，必传 record_id）
    """
    try:
        from app.services.platform_notification_service import platform_notification_service

        if payload.type == "visit_record_card":
            result = _handle_visit_record_card_push(db_session, payload)
            return {"code": 200, "message": "ok", "result": result}

        # 先根据 type 准备好：recipient_ids / send_fn / message_text

        if payload.type == "review_session":
            if not isinstance(payload.context, dict):
                raise HTTPException(status_code=422, detail="review_session requires context object")

            stage = (payload.context.get("stage") or "").strip()
            if not stage:
                raise HTTPException(status_code=422, detail="review_session requires context.stage (CRMReviewSession.stage)")
            session_id = (payload.context.get("session_id") or "").strip()
            if not session_id:
                raise HTTPException(status_code=422, detail="review_session requires context.session_id")

            # 仅这两个 stage 会发推送，其余 stage 静默跳过（不报错）
            if stage not in REVIEW_STAGE_CONFIG:
                logger.info("Review job: stage=%s does not trigger notification", stage)
                return {
                    "code": 200,
                    "message": "ok",
                    "result": {
                        "success": False,
                        "recipients_count": 0,
                        "success_count": 0,
                        "failed_recipients": [],
                    },
                }

            recipient_ids = _normalize_recipient_user_ids(
                _resolve_review_recipients_by_stage(db_session, session_id, stage)
            )
            if not recipient_ids:
                raise HTTPException(status_code=422, detail="recipient_user_ids is required")

            # 文案：按阶段区分（内部用 session_id 统一拼跳转链接）
            message_text = _build_review_session_message(stage, session_id)

            if settings.CRM_REVIEW_SESSION_NOTIFICATION_ENABLED:
                send_fn = platform_notification_service.send_review_session_notification
            else:
                # 暂不做真实推送：仅记录本次任务信息，后续按配置开启发送。
                logger.info(
                    "Review notification recorded only: stage=%s session_id=%s recipients=%s message=%s",
                    stage,
                    session_id,
                    recipient_ids,
                    message_text,
                )
                return {
                    "code": 200,
                    "message": "ok",
                    "result": {
                        "success": True,
                        "recorded_only": True,
                        "recipients_count": len(recipient_ids),
                        "success_count": 0,
                        "failed_recipients": [],
                    },
                }

        else:
            recipient_ids = _normalize_recipient_user_ids(payload.recipient_user_ids)
            if not recipient_ids:
                raise HTTPException(status_code=422, detail="recipient_user_ids is required")

            if payload.type == "sales_task_created":
                task_id = (payload.task_id or "").strip()
                if not task_id:
                    raise HTTPException(status_code=422, detail="sales_task_created requires task_id")
                message_text = _build_sales_task_created_message(payload)
                send_fn = platform_notification_service.send_sales_task_created_notification
            else:
                # 统一消息格式：
                # {author}{title}\n[{link_text}]({jump_url})\n{label}：{content}\n
                author = (payload.author_name or "").strip() or "有人"
                link_text = (payload.link_text or "").strip() or "查看详情"
                jump_url = (payload.jump_url or "").strip()
                link_line = f"[{link_text}]({jump_url})" if jump_url else ((payload.link_text or "").strip() or "")
                content_preview = (payload.content or "").strip()
                if len(content_preview) > 200:
                    content_preview = content_preview[:197] + "..."
                content_preview = content_preview or "--"

                if payload.type == "weekly_followup_comment":
                    title, label = "评论了你的周跟进总结", "评论"
                    send_fn = platform_notification_service.send_weekly_followup_comment_notification
                else:
                    title, label = "评论了你的拜访记录", "评论"
                    send_fn = platform_notification_service.send_visit_record_comment_notification

                message_text = f"{author}{title}\n"
                if link_line:
                    message_text += f"{link_line}\n"
                message_text += f"{label}：{content_preview}\n"

        # 批量发送并汇总结果
        success_count = 0
        failed: List[dict] = []
        for rid in recipient_ids:
            r = send_fn(db_session, recipient_user_id=rid, message_text=message_text)
            if r.get("success"):
                success_count += 1
            else:
                failed.append({"recipient_user_id": rid, "message": r.get("message")})

        result = {
            "success": success_count > 0,
            "recipients_count": len(recipient_ids),
            "success_count": success_count,
            "failed_recipients": failed,
        }
        return {"code": 200, "message": "ok", "result": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to push notification: {e}")
        raise HTTPException(status_code=500, detail="Failed to push notification")