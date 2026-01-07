import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.core.config import settings
from app.feishu.push_reports import push_weekly_reports

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/notification"
)


class PushNotificationRequest(BaseModel):
    """
    统一推送接口请求体：
    - weekly_followup_comment: 周跟进总结评论提醒（文本消息）
    - visit_record_comment: 拜访记录评论提醒（文本消息）
    - sales_task_created: 外部服务创建销售任务后推送（文本消息）
    """

    type: Literal["weekly_followup_comment", "visit_record_comment", "sales_task_created"]

    # 接收人
    recipient_user_ids: Optional[List[str]] = None

    # 消息作者（可选）
    author_name: Optional[str] = None

    # 跳转链接与展示文本（链接可选；展示文本可选）
    jump_url: Optional[str] = None
    link_text: Optional[str] = None

    # 内容摘要（评论内容 / 任务标题等，允许为空）
    content: Optional[str] = None


@router.post("/push-weekly-reports", deprecated=True)
def push_weekly_reports_api(
    db_session: SessionDep,
    report_type: Literal["review1", "review5"] = Body(..., example="review1"),
    external: bool = Body(False, example=False),
    platform: Optional[Literal["feishu", "lark"]] = Body(None, example="feishu"),
    items: List[dict] = Body(..., example=[
        {"execution_id": "1234567890", "report_name": "业绩及变化统计表-[2025-06-01 - 2025-06-06]"}
    ]),
    receivers: Optional[List[dict]] = Body(None, example=[
        {"name": "张三", "email": "zhangsan@aptsell.ai", "open_id": "ou_1234567890"}
    ])
):    
    try:
        if not items:
            logger.info("No weekly reports to push")
            return {"code": 200, "message": "ok"} 

        push_weekly_reports(items, receivers, report_type, external, platform, db_session)
        return {"code": 200, "message": "ok"} 

    except Exception as e:
        logger.error(f"Failed to push weekly reports: {e}")
        raise HTTPException(status_code=500, detail="Failed to push weekly reports")


@router.post("/push")
async def push_notification_api(
    payload: PushNotificationRequest,
    db_session: SessionDep,
):
    """
    统一消息推送入口（支持 API Key 鉴权）：
    1) 周总结保存 comments 后给负责人推送（weekly_followup_comment）
    2) 拜访记录保存 comments 后给跟进人推送（visit_record_comment）
    3) 外部服务创建销售任务后推送（sales_task_created）
    """
    try:
        from app.services.platform_notification_service import platform_notification_service

        # 统一接收人列表（去重/去空）
        raw_ids: List[str] = []
        if payload.recipient_user_ids:
            raw_ids.extend([str(x) for x in payload.recipient_user_ids if x is not None])

        recipient_ids = []
        seen = set()
        for rid in raw_ids:
            rid = str(rid or "").strip()
            if not rid or rid in seen:
                continue
            seen.add(rid)
            recipient_ids.append(rid)

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

        link_line = ""
        if jump_url:
            link_line = f"[{link_text}]({jump_url})"
        else:
            # 无链接时不强行拼 markdown 链接，避免无效格式；仅在明确传入 link_text 时显示
            raw_link_text = (payload.link_text or "").strip()
            link_line = raw_link_text

        content_preview = (payload.content or "").strip()
        if len(content_preview) > 200:
            content_preview = content_preview[:197] + "..."
        content_preview = content_preview or "--"

        if payload.type == "weekly_followup_comment":
            title = "评论了你的周跟进总结"
            label = "评论"
            message_text = f"{author}{title}\n"
            if link_line:
                message_text += f"{link_line}\n"
            message_text += f"{label}：{content_preview}\n"
            send_fn = platform_notification_service.send_weekly_followup_comment_notification
        elif payload.type == "visit_record_comment":
            title = "评论了你的拜访记录"
            label = "评论"
            message_text = f"{author}{title}\n"
            if link_line:
                message_text += f"{link_line}\n"
            message_text += f"{label}：{content_preview}\n"
            send_fn = platform_notification_service.send_visit_record_comment_notification
        else:
            # sales_task_created
            title = "将你添加为任务的负责人"
            label = "任务"
            message_text = f"{author}{title}\n"
            if link_line:
                message_text += f"{link_line}\n"
            message_text += f"{label}：{content_preview}\n"
            send_fn = platform_notification_service.send_sales_task_created_notification

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