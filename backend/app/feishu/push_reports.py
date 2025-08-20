import logging
from app.core.config import settings
from app.platforms.feishu.client import feishu_client
from app.platforms.lark.client import lark_client
from app.platforms.constants import DEFAULT_INTERNAL_GROUP_CHATS, INTERNAL_APP_IDS
from app.platforms.notification_types import NOTIFICATION_TYPE_REPORT_1, NOTIFICATION_TYPE_REPORT_5
from app.repositories.user_profile import UserProfileRepo

logger = logging.getLogger(__name__)


def push_weekly_reports(items, receivers, report_type, db_session, external=False, platform="feishu"):
   
    if report_type == "review1":
        notification_type = NOTIFICATION_TYPE_REPORT_1
    elif report_type == "review5":
        notification_type = NOTIFICATION_TYPE_REPORT_5
    else:
        logger.warning(f"不支持的report_type: {report_type}")
        return
    
    token = None
    if platform == "feishu":
        token = feishu_client.get_tenant_access_token(external=external)
    elif platform == "lark":
        token = lark_client.get_tenant_access_token(external=external)
    else:
        logger.warning(f"不支持的platform: {platform}")
        return

    # 如果没有传入receivers，才使用默认配置
    if not receivers:
        if external:
            # 外部环境：查找有推送权限的用户
            user_profile_repo = UserProfileRepo()
            users_with_permission = user_profile_repo.get_users_by_notification_permission(
                db_session, notification_type, platform
            )
            
            if users_with_permission:
                receivers = []
                for user in users_with_permission:
                    receivers.append({
                        "name": user.name or "Unknown",
                        "open_id": user.get_platform_open_id(platform),
                        "receive_id_type": "open_id"
                    })
                logger.info(f"外部环境使用权限系统找到 {len(users_with_permission)} 个有周报推送权限的用户")
            else:
                logger.info("外部环境权限系统中没有找到有周报推送权限的用户，跳过推送")
                return
        else:
            # 内部环境：使用群聊配置
            internal_app_id = INTERNAL_APP_IDS[0]
            matching_group = None
            
            for group in DEFAULT_INTERNAL_GROUP_CHATS:
                if group["client_id"] == internal_app_id:
                    matching_group = group
                    break
            
            if matching_group:
                receivers = [{
                    "name": matching_group["name"],
                    "open_id": matching_group["chat_id"],
                    "receive_id_type": "chat_id"
                }]
                logger.info(f"使用匹配的内部群聊: {matching_group['name']} ({matching_group['chat_id']}) for app_id: {internal_app_id}")
            else:
                logger.warning(f"当前应用 {internal_app_id} 没有找到匹配的群聊配置，跳过推送")
    
    logger.info(receivers)
    
    # 组装消息
    text = ""
    if report_type == "review1":
        lines = [
            f"Sia帮您生成了[【{item['report_name']}】]({settings.REVIEW_REPORT_HOST}/review/weeklyDetail/{item['execution_id']})，请点击链接查看详情。"
            for item in items
        ]
        text = '\n'.join(lines)
    elif report_type == "review5":
        lines = [
            f"Sia帮您生成了[【{item['report_name']}】]({settings.REVIEW_REPORT_HOST}/review/muban5Detail/{item['execution_id']})，请点击链接查看详情。"
            for item in items
        ]
        text = '\n'.join(lines)

    
    for receiver in receivers:
        receive_id_type = receiver.get("receive_id_type", "open_id")
        if platform == "feishu":
            resp = feishu_client.send_message(receiver["open_id"], token, text, receive_id_type)
        elif platform == "lark":
            resp = lark_client.send_message(receiver["open_id"], token, text, receive_id_type)
        else:
            logger.warning(f"不支持的platform: {platform}")
            return
        logger.info(f"发送给 {receiver['name']} 结果: {resp}")
