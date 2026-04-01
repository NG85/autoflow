import logging
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import SessionDep
from app.core.config import settings
from app.models.crm_review import CRMReviewAttendee

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
    - review_session: review 阶段推进触发的推送（需要调用方传 context.stage/context.session_id）
    """

    type: Literal["weekly_followup_comment", "visit_record_comment", "sales_task_created", "review_session"]
    context: Optional[Dict[str, Any]] = None

    # 接收人
    recipient_user_ids: Optional[List[str]] = None

    # 消息作者（可选）
    author_name: Optional[str] = None

    # 跳转链接与展示文本（链接可选；展示文本可选）
    jump_url: Optional[str] = None
    link_text: Optional[str] = None

    # 内容摘要（评论内容 / 任务标题等，允许为空）
    content: Optional[str] = None


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
    """
    try:
        from app.services.platform_notification_service import platform_notification_service

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

            send_fn = platform_notification_service.send_review_session_notification

        else:
            recipient_ids = _normalize_recipient_user_ids(payload.recipient_user_ids)
            if not recipient_ids:
                raise HTTPException(status_code=422, detail="recipient_user_ids is required")

            # 统一消息格式：
            # {author}{title}\n[{link_text}]({jump_url})\n{label}：{content}\n
            author = (payload.author_name or "").strip() or "有人"
            link_text = (payload.link_text or "").strip() or "查看详情"
            jump_url = (payload.jump_url or "").strip()

            # 特殊兜底：创建销售任务但未传 jump_url 时，跳转到任务列表页
            if payload.type == "sales_task_created" and not jump_url:
                jump_url = (settings.CRM_SALES_TASK_PAGE_URL or "").strip()

            link_line = f"[{link_text}]({jump_url})" if jump_url else ((payload.link_text or "").strip() or "")
            content_preview = (payload.content or "").strip()
            if len(content_preview) > 200:
                content_preview = content_preview[:197] + "..."
            content_preview = content_preview or "--"

            if payload.type == "weekly_followup_comment":
                title, label = "评论了你的周跟进总结", "评论"
                send_fn = platform_notification_service.send_weekly_followup_comment_notification
            elif payload.type == "visit_record_comment":
                title, label = "评论了你的拜访记录", "评论"
                send_fn = platform_notification_service.send_visit_record_comment_notification
            else:
                # sales_task_created
                title, label = "将你添加为任务的负责人", "任务"
                send_fn = platform_notification_service.send_sales_task_created_notification

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