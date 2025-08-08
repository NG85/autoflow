import logging
from typing import List, Dict, Any
from sqlmodel import Session, select
from app.repositories.user_profile import UserProfileRepo
from app.feishu.common_open import send_feishu_message, get_tenant_access_token, DEFAULT_INTERNAL_GROUP_CHATS, INTERNAL_APP_IDS
from app.core.config import settings

logger = logging.getLogger(__name__)


DEFAULT_EXTERNAL_ADMINS = [
    {
        "name": "龙恒",
        "email": "ls@pingcap.cn",
        "open_id": "ou_adcaafc471d57fc6f9b209c05c0f5ce1",
        "user_id": "01971c23-28be-70de-a08c-6e58e0911491"
    }
]

DEFAULT_EXTERNAL_EXTENDED_ADMINS = DEFAULT_EXTERNAL_ADMINS + [
    {
        "name": "林微",
        "email": "wei.lin@pingcap.cn",
        "open_id": "ou_edbdc2e3fc8eb411bbc49cc586629709",
        "user_id": "0196d251-3fa0-71f8-91d3-9a03a412c954"
    },
]

class FeishuNotificationService:
    """飞书推送服务 - 基于用户档案进行消息推送"""
    
    def __init__(self):
        self.user_profile_repo = UserProfileRepo()
    
    def _get_matching_group_chats(self) -> List[Dict[str, str]]:
        """获取当前应用匹配的群聊"""
        matching_groups = []
        current_app_id = settings.FEISHU_APP_ID
        
        # 如果当前应用ID不在内部应用列表中，使用第一个内部应用ID
        if current_app_id not in INTERNAL_APP_IDS:
            current_app_id = INTERNAL_APP_IDS[0]
            logger.info(f"Current app ID {INTERNAL_APP_ID} not in internal app list, using first internal app ID: {current_app_id}")
        
        for group in DEFAULT_INTERNAL_GROUP_CHATS:
            if group["client_id"] == current_app_id:
                matching_groups.append(group)
        
        return matching_groups
    
    def get_recipients_for_recorder(
        self, 
        db_session: Session, 
        recorder_name: str = None,
        recorder_id: str = None,
        external: bool = False
    ) -> List[Dict[str, Any]]:
        """
        获取记录人相关的推送接收者
        包括：记录人、直属上级、部门负责人
        
        支持通过recorder_name或recorder_id查找
        如果是外部推送，还会增加特别的管理者
        """
        recipients = []
        
        # 1. 查找记录人的档案
        recorder_profile = None
        
        if recorder_id:
            # 优先使用recorder_id查找
            recorder_profile = self.user_profile_repo.get_by_recorder_id(db_session, recorder_id)
        
        if not recorder_profile and recorder_name:
            # 如果recorder_id没找到，再尝试通过姓名查找
            recorder_profile = self.user_profile_repo.get_by_name(db_session, recorder_name)
        
        if not recorder_profile:
            logger.warning(f"No profile found for recorder: name={recorder_name}, id={recorder_id}")
            return recipients
        
        # 2. 添加记录人
        if recorder_profile.feishu_open_id:
            recipients.append({
                "open_id": recorder_profile.feishu_open_id,
                "name": recorder_profile.name or recorder_name or "Unknown",
                "type": "recorder",
                "department": recorder_profile.department
            })
        
        # 3. 添加直属上级
        if recorder_profile.direct_manager_id:
            manager_profile = self.user_profile_repo.get_by_oauth_user_id(
                db_session, recorder_profile.direct_manager_id
            )
            if manager_profile and manager_profile.feishu_open_id:
                recipients.append({
                    "open_id": manager_profile.feishu_open_id,
                    "name": manager_profile.name or manager_profile.direct_manager_name,
                    "type": "direct_manager",
                    "department": manager_profile.department
                })
        
        # 4. 添加部门负责人
        if recorder_profile.department:
            dept_manager = self.user_profile_repo.get_department_manager(
                db_session, recorder_profile.department
            )
            if dept_manager and dept_manager.feishu_open_id:
                # 避免重复推送（如果部门负责人就是直属上级）
                if not any(r["open_id"] == dept_manager.feishu_open_id for r in recipients):
                    recipients.append({
                        "open_id": dept_manager.feishu_open_id,
                        "name": dept_manager.name or dept_manager.direct_manager_name,
                        "type": "department_manager",
                        "department": dept_manager.department
                    })
        
        # 5. 如果是外部推送，添加特别管理者
        if external:
            for admin in DEFAULT_EXTERNAL_ADMINS:
                # 避免重复推送（如果特别管理者已经在接收者列表中）
                if not any(r["open_id"] == admin["open_id"] for r in recipients):
                    recipients.append({
                        "open_id": admin["open_id"],
                        "name": admin["name"],
                        "type": "external_admin",
                        "department": "管理团队"
                    })
        
        # 6. 如果不是外部推送，添加群聊
        if not external:
            matching_groups = self._get_matching_group_chats()
            for group in matching_groups:
                recipients.append({
                    "open_id": group["chat_id"],
                    "name": group["name"],
                    "type": "group_chat",
                    "department": "群聊",
                    "receive_id_type": "chat_id"  # 群聊使用chat_id
                })
        
        return recipients
    
    # 发送拜访记录通知 - 实时推送给记录人、直属上级、部门负责人
    def send_visit_record_notification(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None,
        visit_record: Dict[str, Any] = None,
        visit_type: str = "form",
        external: bool = False
    ) -> Dict[str, Any]:
        """
        发送拜访记录通知
        
        支持通过recorder_name或recorder_id查找记录人
        """
        # 获取接收者列表
        recipients = self.get_recipients_for_recorder(
            db_session, 
            recorder_name=recorder_name, 
            recorder_id=recorder_id,
            external=external
        )
        
        if not recipients:
            logger.warning(f"No recipients found for recorder: name={recorder_name}, id={recorder_id}")
            return {
                "success": False,
                "message": "No recipients found",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备消息内容
        template_id = "AAqz0J0JSTciO" if visit_type == "form" else "AAqz0v4nx70HL"
        template_vars = {
            "visit_date": visit_record.get("visit_communication_date", "--") if visit_record else "--",
            "recorder": recorder_name or "--",
            "department": visit_record.get("department", "--") if visit_record else "--",
            "sales_visit_records": [visit_record] if visit_record else []
        }
        
        card_content = {
            "type": "template",
            "data": {
                "template_id": template_id,
                "template_variable": template_vars
            }
        }
        
        # 获取飞书访问令牌
        try:
            token = get_tenant_access_token(
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET,
                external=external
            )
        except Exception as e:
            logger.error(f"Failed to get tenant access token: {e}")
            return {
                "success": False,
                "message": f"Failed to get token: {str(e)}",
                "recipients_count": len(recipients),
                "success_count": 0
            }
        
        # 逐个推送消息
        success_count = 0
        failed_recipients = []
        
        for recipient in recipients:
            try:
                # 确定接收者ID类型
                receive_id_type = recipient.get("receive_id_type", "open_id")
                
                send_feishu_message(
                    recipient["open_id"],
                    token,
                    card_content,
                    receive_id_type=receive_id_type,
                    msg_type="interactive"
                )
                logger.info(
                    f"Successfully pushed visit record to {recipient['name']} "
                    f"({recipient['type']}) in {recipient.get('department', 'Unknown')}"
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to push message to {recipient['name']}: {e}")
                failed_recipients.append({
                    "name": recipient["name"],
                    "type": recipient["type"],
                    "error": str(e)
                })
        
        result = {
            "success": success_count > 0,
            "message": f"Pushed to {success_count}/{len(recipients)} recipients",
            "recipients_count": len(recipients),
            "success_count": success_count,
            "failed_recipients": failed_recipients
        }
        
        logger.info(f"Visit record notification result: {result}")
        return result
