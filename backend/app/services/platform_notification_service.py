import logging
from typing import List, Dict, Any, Optional
from app.platforms.notification_types import (
    NOTIFICATION_TYPE_DAILY_REPORT,
    NOTIFICATION_TYPE_VISIT_RECORD,
    NOTIFICATION_TYPE_WEEKLY_REPORT,
)
from sqlmodel import Session
from app.repositories.user_profile import UserProfileRepo
from app.repositories.oauth_user import oauth_user_repo
from app.platforms.constants import DEFAULT_INTERNAL_GROUP_CHATS, INTERNAL_APP_IDS, PLATFORM_FEISHU, PLATFORM_LARK
from app.platforms.feishu.client import feishu_client
from app.platforms.lark.client import lark_client
from app.core.config import settings

logger = logging.getLogger(__name__)

class PlatformNotificationService:
    """多平台推送服务 - 基于用户档案进行消息推送，支持飞书和Lark"""
    
    def __init__(self):
        self.user_profile_repo = UserProfileRepo()
    
    def _get_matching_group_chats(self, platform: str = PLATFORM_FEISHU) -> List[Dict[str, str]]:
        """获取当前应用匹配的群聊 - 仅限内部应用"""
        matching_groups = []
        
        if platform == PLATFORM_FEISHU:
            current_app_id = settings.FEISHU_APP_ID
        elif platform == PLATFORM_LARK:
            current_app_id = settings.LARK_APP_ID
        else:
            logger.warning(f"Unsupported platform: {platform}")
            return matching_groups
        
        # 只有内部应用才返回群聊
        if current_app_id not in INTERNAL_APP_IDS:
            logger.info(f"Current app ID {current_app_id} is not in internal app list, no group chats will be returned")
            return matching_groups
        
        for group in DEFAULT_INTERNAL_GROUP_CHATS:
            if group["client_id"] == current_app_id:
                matching_groups.append(group)
        
        return matching_groups
    
    def _get_tenant_access_token(self, platform: str = PLATFORM_FEISHU, external: bool = False) -> str:
        """获取指定平台的租户访问令牌"""
        if platform == PLATFORM_FEISHU:
            return feishu_client.get_tenant_access_token()
        elif platform == PLATFORM_LARK:
            return lark_client.get_tenant_access_token()
        else:
            raise ValueError(f"Unsupported platform: {platform}")
    
    def _send_message(self, open_id: str, token: str, content: Dict[str, Any], platform: str = PLATFORM_FEISHU, **kwargs) -> Dict[str, Any]:
        """发送消息到指定平台"""
        if platform == PLATFORM_FEISHU:
            return feishu_client.send_message(open_id, token, content, **kwargs)
        elif platform == PLATFORM_LARK:
            return lark_client.send_message(open_id, token, content, **kwargs)
        else:
            raise ValueError(f"Unsupported platform: {platform}")
    
    def _group_recipients_by_platform(self, recipients: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """将接收者列表按平台分组"""
        recipients_by_platform = {}
        for recipient in recipients:
            platform = recipient.get("platform", PLATFORM_FEISHU)
            if platform not in recipients_by_platform:
                recipients_by_platform[platform] = []
            recipients_by_platform[platform].append(recipient)
        return recipients_by_platform
    
    def _get_platform_tokens(self, platforms: List[str]) -> Dict[str, str]:
        """批量获取多个平台的访问令牌"""
        platform_tokens = {}
        for platform in platforms:
            if platform not in platform_tokens:
                try:
                    platform_tokens[platform] = self._get_tenant_access_token(platform)
                    logger.info(f"Successfully obtained token for platform: {platform}")
                except Exception as e:
                    logger.error(f"Failed to get {platform} tenant access token: {e}")
                    # 不在这里抛出异常，让调用方处理
        return platform_tokens
    
    def _validate_platform_support(self, platform: str) -> bool:
        """验证平台是否支持"""
        return platform in [PLATFORM_FEISHU, PLATFORM_LARK]
    
    def _create_failed_recipient_record(self, recipient: Dict[str, Any], platform: str, error: str) -> Dict[str, Any]:
        """创建失败接收者记录"""
        return {
            "name": recipient["name"],
            "type": recipient["type"],
            "platform": platform,
            "error": error
        }
    
    def _send_messages_to_platform(
        self,
        platform: str,
        platform_recipients: List[Dict[str, Any]],
        token: str,
        card_content: Dict[str, Any],
        template_id: str = None,
        template_vars: Dict[str, Any] = None
    ) -> tuple[int, List[Dict[str, Any]]]:
        """
        向指定平台的所有接收者发送消息
        
        Args:
            platform: 平台名称
            platform_recipients: 该平台的接收者列表
            token: 平台访问令牌
            card_content: 卡片内容（如果提供，直接使用）
            template_id: 模板ID（如果提供，会构建card_content）
            template_vars: 模板变量（如果提供，会构建card_content）
            
        Returns:
            (成功数量, 失败接收者列表)
        """
        success_count = 0
        failed_recipients = []
        
        # 如果提供了模板信息，构建卡片内容
        if template_id and template_vars and not card_content:
            card_content = {
                "type": "template",
                "data": {
                    "template_id": template_id,
                    "template_variable": template_vars
                }
            }
        
        for recipient in platform_recipients:
            try:
                # 确定接收者ID类型
                receive_id_type = recipient.get("receive_id_type", "open_id")
                
                self._send_message(
                    recipient["open_id"],
                    token,
                    card_content,
                    platform,
                    receive_id_type=receive_id_type,
                    msg_type="interactive"
                )
                
                logger.info(
                    f"Successfully pushed message to {recipient['name']} "
                    f"({recipient['type']}) on {platform}"
                )
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to send message to {recipient['name']} on {platform}: {e}")
                failed_recipients.append(
                    self._create_failed_recipient_record(recipient, platform, str(e))
                )
        
        return success_count, failed_recipients
    
    def _send_notifications_by_platform(
        self,
        recipients_by_platform: Dict[str, List[Dict[str, Any]]],
        card_content: Dict[str, Any] = None,
        template_id: str = None,
        template_vars: Dict[str, Any] = None,
        notification_type: str = "notification"
    ) -> Dict[str, Any]:
        """
        按平台分组发送通知的核心方法
        
        Args:
            recipients_by_platform: 按平台分组的接收者字典
            card_content: 卡片内容（如果提供，直接使用）
            template_id: 模板ID（如果提供，会构建card_content）
            template_vars: 模板变量（如果提供，会构建card_content）
            notification_type: 通知类型，用于日志
            
        Returns:
            推送结果
        """
        if not recipients_by_platform:
            return {
                "success": False,
                "message": f"No recipients found for {notification_type}",
                "recipients_count": 0,
                "success_count": 0,
                "platforms_used": [],
                "failed_recipients": []
            }
        
        total_success_count = 0
        total_failed_recipients = []
        
        # 获取所有需要的平台token
        platforms = list(recipients_by_platform.keys())
        platform_tokens = self._get_platform_tokens(platforms)
        
        for platform, platform_recipients in recipients_by_platform.items():
            # 验证平台支持
            if not self._validate_platform_support(platform):
                logger.warning(f"Skipping unsupported platform: {platform}")
                # 记录该平台所有接收人的失败
                for recipient in platform_recipients:
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(
                            recipient, platform, f"Unsupported platform: {platform}"
                        )
                    )
                continue
            
            # 检查是否有token
            if platform not in platform_tokens:
                logger.error(f"No token available for platform: {platform}")
                # 记录该平台所有接收人的失败
                for recipient in platform_recipients:
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(
                            recipient, platform, "Failed to get token"
                        )
                    )
                continue
            
            # 发送消息到当前平台
            token = platform_tokens[platform]
            success_count, failed_recipients = self._send_messages_to_platform(
                platform, platform_recipients, token, card_content, template_id, template_vars
            )
            
            total_success_count += success_count
            total_failed_recipients.extend(failed_recipients)
        
        # 统计各平台的结果
        platforms_used = list(recipients_by_platform.keys())
        total_recipients_count = sum(len(recipients) for recipients in recipients_by_platform.values())
        
        return {
            "success": total_success_count > 0,
            "message": f"{notification_type} sent to {total_success_count}/{total_recipients_count} recipients across platforms: {', '.join(platforms_used)}",
            "recipients_count": total_recipients_count,
            "success_count": total_success_count,
            "platforms_used": platforms_used,
            "failed_recipients": total_failed_recipients
        }
    
    
    
    def get_recipients_for_recorder(
        self, 
        db_session: Session, 
        recorder_name: str = None,
        recorder_id: str = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        获取记录人相关的推送接收者，按平台分组
        包括：记录人、直属上级、部门负责人
        
        支持通过recorder_name或recorder_id查找
        返回按平台分组的接收者字典
        """
        recipients_by_platform = {}
        
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
            return recipients_by_platform
        
        # 2. 添加记录人
        recorder_open_id = recorder_profile.open_id
        
        # 如果profile没有open_id，尝试从OAuthUser表中查找
        if not recorder_open_id:
            oauth_user = None
            
            # 优先通过recorder_id (ask_id) 查找
            if recorder_id:
                oauth_user = oauth_user_repo.get_by_ask_id(db_session, recorder_id)
            
            # 如果通过recorder_id没找到，再尝试通过姓名查找
            if not oauth_user and recorder_name:
                oauth_user = oauth_user_repo.get_by_name(db_session, recorder_name)
            
            if oauth_user and oauth_user.open_id:
                recorder_open_id = oauth_user.open_id
                # 根据OAuthUser的channel设置platform
                if oauth_user.channel in ['feishu', 'feishuBot']:
                    platform = PLATFORM_FEISHU
                elif oauth_user.channel in ['lark', 'larkBot']:
                    platform = PLATFORM_LARK
                else:
                    platform = PLATFORM_FEISHU  # 默认使用飞书
                logger.info(f"Found open_id for recorder from OAuthUser table: {recorder_open_id}, platform: {platform} (channel: {oauth_user.channel})")
        
        if recorder_open_id:
            # 如果没有从OAuthUser获取到platform，使用profile的platform或默认值
            if 'platform' not in locals():
                platform = recorder_profile.platform or PLATFORM_FEISHU
            # 只支持飞书和Lark平台
            if platform not in [PLATFORM_FEISHU, PLATFORM_LARK]:
                logger.warning(f"Recorder platform {platform} not supported, skipping")
            else:
                if platform not in recipients_by_platform:
                    recipients_by_platform[platform] = []
                
                recipients_by_platform[platform].append({
                    "open_id": recorder_open_id,
                    "name": recorder_profile.name or recorder_name or "Unknown",
                    "type": "recorder",
                    "department": recorder_profile.department,
                    "platform": platform
                })
        
        # 3. 添加直属上级
        if recorder_profile.direct_manager_id:
            manager_profile = self.user_profile_repo.get_by_oauth_user_id(
                db_session, recorder_profile.direct_manager_id
            )
            if manager_profile:
                manager_open_id = manager_profile.open_id
                if manager_open_id:
                    platform = manager_profile.platform or PLATFORM_FEISHU
                    # 只支持飞书和Lark平台
                    if platform not in [PLATFORM_FEISHU, PLATFORM_LARK]:
                        logger.warning(f"Manager platform {platform} not supported, skipping")
                    else:
                        if platform not in recipients_by_platform:
                            recipients_by_platform[platform] = []
                        
                        recipients_by_platform[platform].append({
                            "open_id": manager_open_id,
                            "name": manager_profile.name or recorder_profile.direct_manager_name,
                            "type": "direct_manager",
                            "department": manager_profile.department,
                            "platform": platform
                        })
        
        # 4. 添加部门负责人
        if recorder_profile.department:
            dept_manager = self.user_profile_repo.get_department_manager(
                db_session, recorder_profile.department
            )
            if dept_manager:
                dept_manager_open_id = dept_manager.open_id
                if dept_manager_open_id:
                    platform = dept_manager.platform or PLATFORM_FEISHU
                    # 只支持飞书和Lark平台
                    if platform not in [PLATFORM_FEISHU, PLATFORM_LARK]:
                        logger.warning(f"Department manager platform {platform} not supported, skipping")
                    else:
                        if platform not in recipients_by_platform:
                            recipients_by_platform[platform] = []
                        
                        # 避免重复推送（如果部门负责人就是直属上级）
                        existing_open_ids = [r["open_id"] for r in recipients_by_platform[platform]]
                        if dept_manager_open_id not in existing_open_ids:
                            recipients_by_platform[platform].append({
                                "open_id": dept_manager_open_id,
                                "name": dept_manager.name,
                                "type": "department_manager",
                                "department": dept_manager.department,
                                "platform": platform
                            })
        
        # 5. 根据应用ID判断推送类型
        current_app_id = settings.FEISHU_APP_ID if recorder_profile.platform == PLATFORM_FEISHU else settings.LARK_APP_ID
        is_internal_app = current_app_id in INTERNAL_APP_IDS
        
        if is_internal_app:
            # 内部应用：添加群聊
            matching_groups = self._get_matching_group_chats(recorder_profile.platform)
            platform = recorder_profile.platform or PLATFORM_FEISHU
            # 只支持飞书和Lark平台
            if platform not in [PLATFORM_FEISHU, PLATFORM_LARK]:
                logger.warning(f"Internal app platform {platform} not supported, skipping group chats")
            else:
                if platform not in recipients_by_platform:
                    recipients_by_platform[platform] = []
                
                for group in matching_groups:
                    recipients_by_platform[platform].append({
                        "open_id": group["chat_id"],
                        "name": group["name"],
                        "type": "group_chat",
                        "department": "群聊",
                        "receive_id_type": "chat_id",  # 群聊使用chat_id
                        "platform": platform
                    })
        else:
            # 外部应用：从profile中查询可以接收拜访记录的人员名单
            visit_receivers = self.user_profile_repo.get_users_by_notification_permission(
                db_session, NOTIFICATION_TYPE_VISIT_RECORD
            )
            for receiver in visit_receivers:
                receiver_open_id = receiver.open_id
                if receiver_open_id:
                    platform = receiver.platform or PLATFORM_FEISHU
                    # 只支持飞书和Lark平台
                    if platform not in [PLATFORM_FEISHU, PLATFORM_LARK]:
                        logger.warning(f"External receiver platform {platform} not supported, skipping")
                    else:
                        if platform not in recipients_by_platform:
                            recipients_by_platform[platform] = []
                        
                        # 避免重复推送（如果该人员已经在接收者列表中）
                        existing_open_ids = [r["open_id"] for r in recipients_by_platform[platform]]
                        if receiver_open_id not in existing_open_ids:
                            recipients_by_platform[platform].append({
                                "open_id": receiver_open_id,
                                "name": receiver.name,
                                "type": "external_admin",
                                "department": receiver.department or "管理团队",
                                "platform": platform
                            })
        
        return recipients_by_platform
    
    def get_recipients_for_daily_report(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None,
        department_name: str = None
    ) -> List[Dict[str, Any]]:
        """获取日报推送的接收者 - 只推送给销售本人"""
        
        recipients = []
        
        # 1. 查找记录人的档案
        recorder_profile = None
        
        if recorder_id:
            # 优先使用recorder_id查找
            recorder_profile = self.user_profile_repo.get_by_recorder_id(db_session, recorder_id)
            if recorder_profile:
                logger.info(f"Found profile by recorder_id: {recorder_id} -> {recorder_profile.name}")
        
        if not recorder_profile and recorder_name:
            # 如果recorder_id没找到，使用姓名和部门组合查找（更精确）
            if department_name:
                recorder_profile = self.user_profile_repo.get_by_name_and_department(
                    db_session, recorder_name, department_name
                )
                if recorder_profile:
                    logger.info(f"Found profile by name+department: {recorder_name} in {department_name} -> {recorder_profile.name}")
            else:
                # 如果没有部门信息，只按姓名查找
                recorder_profile = self.user_profile_repo.get_by_name(db_session, recorder_name)
                if recorder_profile:
                    logger.info(f"Found profile by name only: {recorder_name} -> {recorder_profile.name}")
        
        if not recorder_profile:
            logger.warning(f"No profile found for daily report recipient: name={recorder_name}, id={recorder_id}, department={department_name}")
            # 记录详细信息以便调试
            logger.warning(f"Available profiles with similar names:")
            try:
                all_profiles = self.user_profile_repo.get_all_active_profiles(db_session)
                if recorder_name:
                    similar_names = [p.name for p in all_profiles if recorder_name == p.name]
                    if similar_names:
                        logger.warning(f"Exact name matches found: {similar_names}")
                    else:
                        logger.warning(f"No exact name matches found. Available names: {[p.name for p in all_profiles[:10]]}")
            except Exception as e:
                logger.error(f"Error checking available profiles: {e}")
            return recipients
        
        # 2. 只添加记录人本人
        recorder_open_id = recorder_profile.open_id
        if recorder_open_id:
            recipients.append({
                "open_id": recorder_open_id,
                "name": recorder_profile.name or recorder_name or "Unknown",
                "type": "recorder",
                "department": recorder_profile.department,
                "receive_id_type": "open_id",
                "platform": recorder_profile.platform
            })
            logger.info(f"Found daily report recipient: {recorder_profile.name} ({recorder_profile.department}) with {recorder_profile.platform} open_id: {recorder_open_id}")
        else:
            logger.warning(f"Recorder {recorder_name} (profile: {recorder_profile.name}) has no {recorder_profile.platform} open_id, cannot send daily report")
        
        return recipients
    
    # 发送拜访记录通知 - 实时推送给记录人、直属上级、部门负责人
    def send_visit_record_notification(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None,
        visit_record: Dict[str, Any] = None,
        visit_type: str = "form",
        meeting_notes: str = None
    ) -> Dict[str, Any]:
        """
        发送拜访记录通知
        
        支持通过recorder_name或recorder_id查找记录人
        对于link类型的拜访记录，会包含会议纪要总结
        """
        
        # 获取推送接收者（已按平台分组）
        recipients_by_platform = self.get_recipients_for_recorder(
            db_session, 
            recorder_name=recorder_name, 
            recorder_id=recorder_id
        )
        
        # 新增：获取协同参与人的推送接收者
        collaborative_recipients_by_platform = self._get_collaborative_participants_recipients(
            db_session, visit_record
        )
        
        # 合并两个接收者列表
        if collaborative_recipients_by_platform:
            for platform, recipients in collaborative_recipients_by_platform.items():
                if platform in recipients_by_platform:
                    recipients_by_platform[platform].extend(recipients)
                else:
                    recipients_by_platform[platform] = recipients
        
        if not recipients_by_platform:
            logger.warning(f"No recipients found for recorder: name={recorder_name}, id={recorder_id}")
            return {
                "success": False,
                "message": "No recipients found",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备基础消息内容
        # 处理协同参与人参数，转换为用name拼接的文本
        collaborative_participants_text = "--"
        if visit_record and visit_record.get("collaborative_participants"):
            from app.utils.participants_utils import format_collaborative_participants_names
            collaborative_participants_text = format_collaborative_participants_names(
                visit_record.get("collaborative_participants")
            ) or "--"
        
        # 生成dynamic_fields作为独立的模板参数
        dynamic_fields = []
        if visit_record:
            from app.crm.save_engine import generate_dynamic_fields_for_visit_record
            dynamic_fields = generate_dynamic_fields_for_visit_record(visit_record)
        
        base_template_vars = {
            "visit_date": visit_record.get("last_modified_time", "--") if visit_record else "--",
            "recorder": recorder_name or "--",
            "department": visit_record.get("department", "--") if visit_record else "--",
            "sales_visit_records": [visit_record] if visit_record else [],
            "meeting_notes": meeting_notes,
            "collaborative_participants": collaborative_participants_text,
            "dynamic_fields": dynamic_fields  # 新增：动态字段数组参数
        }
        
        # 根据拜访类型、接收者类型和平台确定模板ID
        def get_template_id(recipient_type: str, platform: str, form_type: Optional[str] = None) -> str:
            # 验证平台支持
            if platform not in [PLATFORM_FEISHU, PLATFORM_LARK]:
                logger.warning(f"Unsupported platform: {platform}")
                return None
            
            if visit_type == "form":
                # 检查是否为简易版表单
                form_type = form_type or settings.CRM_VISIT_RECORD_FORM_TYPE.value
                
                if form_type == "simple":
                    # 简易版表单模板
                    if recipient_type == "recorder":
                        return "AAqzQK6iUiK2k"  # 销售个人卡片：简易版
                    elif recipient_type == "collaborative_participant":
                        return "AAqzQK6iUiK2k"  # 协同参与人卡片：简易版
                    else:
                        return "AAqzQKvKzOW1z"  # leader和管理者卡片：简易版
                else:
                    # 完整版表单模板
                    if recipient_type == "recorder":
                        return "AAqzzmP2uT85t"  # 销售个人卡片：完整版
                    elif recipient_type == "collaborative_participant":
                        return "AAqzzmP2uT85t"  # 协同参与人卡片：完整版
                    else:
                        return "AAqz0J0JSTciO"  # leader和管理者卡片：完整版
            else:
                return "AAqz0v4nx70HL"  # link类型使用通用卡片：会议纪要版
        
        # 逐个平台推送消息（因为需要根据接收者类型选择不同模板）
        total_success_count = 0
        total_failed_recipients = []
        
        # 获取所有需要的平台token
        platforms = list(recipients_by_platform.keys())
        platform_tokens = self._get_platform_tokens(platforms)
        
        for platform, platform_recipients in recipients_by_platform.items():
            # 验证平台支持
            if not self._validate_platform_support(platform):
                logger.warning(f"Skipping unsupported platform: {platform}")
                # 记录该平台所有接收人的失败
                for recipient in platform_recipients:
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(
                            recipient, platform, f"Unsupported platform: {platform}"
                        )
                    )
                continue
            
            # 检查是否有token
            if platform not in platform_tokens:
                logger.error(f"No token available for platform: {platform}")
                # 记录该平台所有接收人的失败
                for recipient in platform_recipients:
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(
                            recipient, platform, "Failed to get token"
                        )
                    )
                continue
            
            # 为当前平台的所有接收人推送消息（使用同一个token）
            token = platform_tokens[platform]
            for recipient in platform_recipients:
                try:
                    # 根据接收者类型和平台选择模板ID
                    template_id = get_template_id(recipient["type"], platform, visit_record.get("form_type"))
                    
                    # 如果模板ID为None，跳过该接收人
                    if template_id is None:
                        logger.warning(f"No template available for recipient {recipient['name']} on platform {platform}")
                        total_failed_recipients.append(
                            self._create_failed_recipient_record(
                                recipient, platform, f"No template available for platform: {platform}"
                            )
                        )
                        continue
                    
                    # 构建卡片内容
                    card_content = {
                        "type": "template",
                        "data": {
                            "template_id": template_id,
                            "template_variable": base_template_vars
                        }
                    }
                    
                    # 发送消息
                    receive_id_type = recipient.get("receive_id_type", "open_id")
                    self._send_message(
                        recipient["open_id"],
                        token,
                        card_content,
                        platform,
                        receive_id_type=receive_id_type,
                        msg_type="interactive"
                    )
                    
                    logger.info(
                        f"Successfully pushed visit record to {recipient['name']} "
                        f"({recipient['type']}) in {recipient.get('department', 'Unknown')} "
                        f"using template {template_id} on {platform}"
                    )
                    total_success_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to push message to {recipient['name']} on {platform}: {e}")
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(recipient, platform, str(e))
                    )
        
        # 统计各平台的结果
        platforms_used = list(recipients_by_platform.keys())
        total_recipients_count = sum(len(recipients) for recipients in recipients_by_platform.values())
        result = {
            "success": total_success_count > 0,
            "message": f"Pushed to {total_success_count}/{total_recipients_count} recipients across platforms: {', '.join(platforms_used)}",
            "recipients_count": total_recipients_count,
            "success_count": total_success_count,
            "platforms_used": platforms_used,
            "failed_recipients": total_failed_recipients
        }
        
        logger.info(f"Visit record notification result: {result}")
        return result
    
    def send_daily_report_notification(
        self,
        db_session: Session,
        daily_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送CRM日报飞书卡片通知"""
        
        recorder_id = daily_report_data.get("recorder_id")
        recorder_name = daily_report_data.get("recorder")
        if not recorder_name:
            logger.warning("Daily report data missing recorder name")
            return {
                "success": False,
                "message": "Missing recorder name in daily report data",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 从日报数据中提取部门信息
        department_name = daily_report_data.get("department_name")
        
        # 获取推送对象 - 个人日报只推送给销售本人
        recipients = self.get_recipients_for_daily_report(
            db_session=db_session,
            recorder_name=recorder_name,
            recorder_id=recorder_id,
            department_name=department_name
        )
        
        if not recipients:
            logger.warning(f"No recipients found for daily report of {recorder_name}")
            return {
                "success": False,
                "message": f"No recipients found for {recorder_name}",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备CRM日报卡片消息内容
        template_id = "AAqzUJ4fpg5XQ"  # CRM日报卡片模板ID
        template_vars = self._convert_daily_report_data_for_feishu(daily_report_data)
        
        card_content = {
            "type": "template",
            "data": {
                "template_id": template_id,
                "template_variable": template_vars
            }
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            card_content=card_content,
            notification_type="daily report"
        )
    
    def get_recipients_for_department_report(
        self,
        db_session: Session,
        department_name: str
    ) -> List[Dict[str, Any]]:
        """获取部门日报推送的接收者"""
        recipients = []
        
        # 查找部门负责人
        dept_manager = self.user_profile_repo.get_department_manager(
            db_session, department_name
        )
        
        if dept_manager:
            dept_manager_open_id = dept_manager.open_id
            if dept_manager_open_id:
                recipients.append({
                    "open_id": dept_manager_open_id,
                    "name": dept_manager.name or dept_manager.direct_manager_name,
                    "type": "department_manager",
                    "department": department_name,
                    "receive_id_type": "open_id",
                    "platform": dept_manager.platform
                })
                logger.info(f"Found department manager for {department_name} on {dept_manager.platform}: {dept_manager.name}")
            else:
                logger.warning(f"Department manager {dept_manager.name} has no {dept_manager.platform} open_id")
        else:
            logger.warning(f"No department manager found for department: {department_name}")
        
        return recipients
    
    def send_department_report_notification(
        self,
        db_session: Session,
        department_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送部门日报飞书卡片通知"""
        
        department_name = department_report_data.get("department_name")
        if not department_name:
            logger.warning("Department report data missing department name")
            return {
                "success": False,
                "message": "Missing department name in department report data",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 获取推送对象 - 部门负责人
        recipients = self.get_recipients_for_department_report(
            db_session=db_session,
            department_name=department_name
        )
        
        if not recipients:
            logger.warning(f"No department manager found for department report of {department_name}")
            return {
                "success": False,
                "message": f"No department manager found for {department_name}",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备部门日报卡片消息内容
        template_id = "AAqz3wUpXTF3g"  # 部门日报卡片模板ID
        template_vars = self._convert_daily_report_data_for_feishu(department_report_data)
        # 确保日期字段是字符串格式
        if 'report_date' in template_vars and hasattr(template_vars['report_date'], 'isoformat'):
            template_vars['report_date'] = template_vars['report_date'].isoformat()
        
        card_content = {
            "type": "template",
            "data": {
                "template_id": template_id,
                "template_variable": template_vars
            }
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            card_content=card_content,
            notification_type="department report"
        )
    
    def get_recipients_for_company_report(self, db_session: Session) -> List[Dict[str, Any]]:
        """获取公司日报推送的接收者"""
        
        recipients = []
        
        # 从profile中获取可以接收日报的人员
        profiles = self.user_profile_repo.get_users_by_notification_permission(db_session, NOTIFICATION_TYPE_DAILY_REPORT)
        
        for profile in profiles:
            profile_open_id = profile.open_id
            if profile_open_id:
                recipients.append({
                    "open_id": profile_open_id,
                    "name": profile.name,
                    "type": "daily_report_recipient",
                    "department": profile.department or "管理团队",
                    "receive_id_type": "open_id",
                    "platform": profile.platform
                })
                logger.info(f"Added company report recipient: {profile.name} on {profile.platform}")
        
        if not recipients:
            logger.warning(f"No recipients configured for company report")
        
        return recipients
    
    def send_company_report_notification(
        self,
        db_session: Session,
        company_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送公司日报飞书卡片通知"""
        
        # 获取推送对象 - 根据应用ID判断推送目标
        recipients = self.get_recipients_for_company_report(db_session)
        
        if not recipients:
            logger.warning(f"No recipients found for company report")
            return {
                "success": False,
                "message": f"No recipients found for company report",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备公司日报卡片消息内容
        template_id = "AAqz3y0IwJLDp"  # 公司日报卡片模板ID
        template_vars = self._convert_daily_report_data_for_feishu(company_report_data)
        # 确保日期字段是字符串格式
        if 'report_date' in template_vars and hasattr(template_vars['report_date'], 'isoformat'):
            template_vars['report_date'] = template_vars['report_date'].isoformat()
        
        card_content = {
            "type": "template",
            "data": {
                "template_id": template_id,
                "template_variable": template_vars
            }
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            card_content=card_content,
            notification_type="company report"
        )
    
    def send_weekly_report_notification(
        self,
        db_session: Session,
        department_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送部门周报飞书卡片通知"""
        
        department_name = department_report_data.get("department_name")
        if not department_name:
            logger.warning("Department weekly report data missing department name")
            return {
                "success": False,
                "message": "Missing department name in department weekly report data",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 获取推送对象 - 部门负责人
        recipients = self.get_recipients_for_department_report(
            db_session=db_session,
            department_name=department_name,
        )
        
        if not recipients:
            logger.warning(f"No department manager found for department weekly report of {department_name}")
            return {
                "success": False,
                "message": f"No department manager found for {department_name}",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备部门周报卡片消息内容
        template_id = "AAqzdm8MsqNjD"  # 团队周报卡片模板ID
        template_vars = self._convert_weekly_report_data_for_feishu(department_report_data)
        
        card_content = {
            "type": "template",
            "data": {
                "template_id": template_id,
                "template_variable": template_vars
            }
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            card_content=card_content,
            notification_type="weekly report"
        )
    
    def get_recipients_for_company_weekly_report(
        self,
        db_session: Session
    ) -> List[Dict[str, Any]]:
        """获取公司周报推送的接收者"""
        
        recipients = []
        
        # 从profile中获取可以接收周报的人员
        profiles = self.user_profile_repo.get_users_by_notification_permission(
            db_session, NOTIFICATION_TYPE_WEEKLY_REPORT
        )
        
        for profile in profiles:
            profile_open_id = profile.open_id
            if profile_open_id:
                recipients.append({
                    "open_id": profile_open_id,
                    "name": profile.name,
                    "type": "weekly_report_recipient",
                    "department": profile.department or "管理团队",
                    "receive_id_type": "open_id",
                    "platform": profile.platform
                })
                logger.info(f"Added company weekly report recipient: {profile.name} on {profile.platform}")
        
        if not recipients:
            logger.warning(f"No recipients configured for company weekly report")
        
        return recipients
    
    def send_company_weekly_report_notification(
        self,
        db_session: Session,
        company_weekly_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送公司周报飞书卡片通知"""
        
        # 获取推送对象
        all_recipients = self.get_recipients_for_company_weekly_report(db_session)
        
        if not all_recipients:
            logger.warning(f"No recipients found for company weekly report")
            return {
                "success": False,
                "message": f"No recipients found for company weekly report",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备公司周报卡片消息内容
        template_id = "AAqzdMIhll3Et"  # 使用团队周报卡片模板ID
        template_vars = self._convert_weekly_report_data_for_feishu(company_weekly_report_data)
        
        card_content = {
            "type": "template",
            "data": {
                "template_id": template_id,
                "template_variable": template_vars
            }
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(all_recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            card_content=card_content,
            notification_type="company weekly report"
        )
    
    def _convert_weekly_report_data_for_feishu(self, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将周报数据转换为飞书卡片所需的格式（所有数值和日期转换为字符串）
        
        Args:
            report_data: 原始周报数据
            
        Returns:
            转换后的数据，所有数值和日期字段都转换为字符串
        """
        converted_data = report_data.copy()
        
        # 处理statistics数组中的数值字段
        if 'statistics' in converted_data and isinstance(converted_data['statistics'], list):
            for stats in converted_data['statistics']:
                if isinstance(stats, dict):
                    for key, value in stats.items():
                        # 将所有数值转换为字符串
                        if isinstance(value, (int, float)):
                            stats[key] = str(value)
        
        # 处理其他可能的数值和日期字段
        for key, value in converted_data.items():
            if isinstance(value, (int, float)) and key not in ['statistics']:
                converted_data[key] = str(value)
            elif hasattr(value, 'isoformat'):  # 处理date对象
                converted_data[key] = value.isoformat()
        
        return converted_data
    
    def _convert_daily_report_data_for_feishu(self, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将日报数据转换为飞书卡片所需的格式（所有数值和日期转换为字符串）
        
        Args:
            report_data: 原始日报数据
            
        Returns:
            转换后的数据，所有数值和日期字段都转换为字符串
        """
        converted_data = report_data.copy()
        
        # 处理statistics数组中的数值字段
        if 'statistics' in converted_data and isinstance(converted_data['statistics'], list):
            for stats in converted_data['statistics']:
                if isinstance(stats, dict):
                    for key, value in stats.items():
                        # 将所有数值转换为字符串
                        if isinstance(value, (int, float)):
                            stats[key] = str(value)
        
        # 处理其他可能的数值和日期字段
        for key, value in converted_data.items():
            if isinstance(value, (int, float)) and key not in ['statistics']:
                converted_data[key] = str(value)
            elif hasattr(value, 'isoformat'):  # 处理date对象
                converted_data[key] = value.isoformat()
        
        return converted_data
    
    def _get_user_platform_for_notification(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None
    ) -> Optional[str]:
        """
        根据记录人信息获取推送平台
        
        Args:
            db_session: 数据库会话
            recorder_name: 记录人姓名
            recorder_id: 记录人ID
            
        Returns:
            推送平台名称 (feishu/lark) 或 None
        """
        try:
            # 1. 优先通过recorder_id查找用户档案
            recorder_profile = None
            
            if recorder_id:
                recorder_profile = self.user_profile_repo.get_by_recorder_id(db_session, recorder_id)
                if recorder_profile:
                    logger.info(f"Found profile by recorder_id: {recorder_id} -> {recorder_profile.name} (platform: {recorder_profile.platform})")
            
            # 2. 如果recorder_id没找到，再尝试通过姓名查找
            if not recorder_profile and recorder_name:
                recorder_profile = self.user_profile_repo.get_by_name(db_session, recorder_name)
                if recorder_profile:
                    logger.info(f"Found profile by name: {recorder_name} -> {recorder_profile.name} (platform: {recorder_profile.platform})")
            
            # 3. 获取用户的平台信息
            if recorder_profile and recorder_profile.platform:
                platform = recorder_profile.platform
                logger.info(f"Using user's platform for notification: {platform}")
                return platform
            else:
                logger.warning(f"No platform found for recorder: name={recorder_name}, id={recorder_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting user platform for notification: {e}")
            return None

    def _get_collaborative_participants_recipients(
        self, 
        db_session: Session, 
        visit_record: Dict[str, Any]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        获取协同参与人的推送接收者，按平台分组
        通过ask_id从user profile表查询platform和open_id
        
        Args:
            db_session: 数据库会话
            visit_record: 拜访记录数据
            
        Returns:
            按平台分组的协同参与人接收者列表
        """
        if not visit_record or not visit_record.get("collaborative_participants"):
            return {}
        
        collaborative_participants = visit_record.get("collaborative_participants", [])
        
        # 解析协同参与人数据
        from app.utils.participants_utils import parse_collaborative_participants_list
        try:
            collaborative_participants = parse_collaborative_participants_list(collaborative_participants)
        except Exception as e:
            logger.warning(f"Failed to parse collaborative participants: {collaborative_participants}, error: {e}")
            return {}
        
        if not collaborative_participants:
            return {}
        
        recipients_by_platform = {}
        
        for participant in collaborative_participants:
            try:
                # 验证协同参与人数据结构
                if not isinstance(participant, dict):
                    logger.warning(f"Invalid participant format: {participant}")
                    continue
                
                name = participant.get("name")
                ask_id = participant.get("ask_id")
                
                if not name:
                    logger.warning(f"Missing name field in participant: {participant}")
                    continue
                
                # 如果ask_id为空，表示非系统注册人员，不需要推送
                if not ask_id:
                    logger.info(f"Skipping external participant (no ask_id): {name}")
                    continue
                
                # 通过ask_id从user profile表查询用户信息
                user_profile = self.user_profile_repo.get_by_oauth_user_id(db_session, ask_id)
                if not user_profile:
                    logger.warning(f"No user profile found for ask_id: {ask_id}, name: {name}")
                    continue
                
                # 获取用户的平台和open_id信息
                platform = user_profile.platform
                open_id = user_profile.open_id
                
                if not all([platform, open_id]):
                    logger.warning(f"User profile missing platform or open_id for ask_id: {ask_id}, name: {name}")
                    continue
                
                # 验证平台支持
                if platform not in [PLATFORM_FEISHU, PLATFORM_LARK]:
                    logger.warning(f"Unsupported platform for collaborative participant: {platform}")
                    continue
                
                # 构建接收者信息
                recipient = {
                    "name": name,
                    "open_id": open_id,
                    "type": "collaborative_participant",  # 新增接收者类型
                    "platform": platform,
                    "receive_id_type": "open_id"
                }
                
                # 按平台分组
                if platform not in recipients_by_platform:
                    recipients_by_platform[platform] = []
                recipients_by_platform[platform].append(recipient)
                
                logger.info(f"Added collaborative participant {name} ({platform}) to recipients")
                
            except Exception as e:
                logger.error(f"Error processing collaborative participant {participant}: {e}")
                continue
        
        return recipients_by_platform


# 创建默认的平台通知服务实例
platform_notification_service = PlatformNotificationService()
