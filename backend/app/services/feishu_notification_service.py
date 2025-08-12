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
    {
        "name": "崔秋",
        "email": "cuiqiu@pingcap.cn",
        "open_id": "ou_718d03819e549537c4dc972154798a81"
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
            logger.info(f"Current app ID {current_app_id} not in internal app list, using first internal app ID: {current_app_id}")
        
        for group in DEFAULT_INTERNAL_GROUP_CHATS:
            if group["client_id"] == current_app_id:
                matching_groups.append(group)
        
        return matching_groups
    
    def get_recipients_for_recorder(
        self, 
        db_session: Session, 
        recorder_name: str = None,
        recorder_id: str = None
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
        
        # 5. 根据应用ID判断推送类型
        current_app_id = settings.FEISHU_APP_ID
        is_internal_app = current_app_id in INTERNAL_APP_IDS
        
        if is_internal_app:
            # 内部应用：添加群聊
            matching_groups = self._get_matching_group_chats()
            for group in matching_groups:
                recipients.append({
                    "open_id": group["chat_id"],
                    "name": group["name"],
                    "type": "group_chat",
                    "department": "群聊",
                    "receive_id_type": "chat_id"  # 群聊使用chat_id
                })
        else:
            # 外部应用：添加特别管理者
            for admin in DEFAULT_EXTERNAL_ADMINS:
                # 避免重复推送（如果特别管理者已经在接收者列表中）
                if not any(r["open_id"] == admin["open_id"] for r in recipients):
                    recipients.append({
                        "open_id": admin["open_id"],
                        "name": admin["name"],
                        "type": "external_admin",
                        "department": "管理团队"
                    })
        
        return recipients
    
    def get_recipients_for_daily_report(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None,
        department_name: str = None
    ) -> List[Dict[str, Any]]:
        """
        获取日报推送的接收者 - 只推送给销售本人
        
        Args:
            db_session: 数据库会话
            recorder_name: 记录人姓名
            recorder_id: 记录人ID
            department_name: 部门名称（用于精确匹配）
            
        Returns:
            接收者列表，只包含销售本人
        """
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
        if recorder_profile.feishu_open_id:
            recipients.append({
                "open_id": recorder_profile.feishu_open_id,
                "name": recorder_profile.name or recorder_name or "Unknown",
                "type": "recorder",
                "department": recorder_profile.department,
                "receive_id_type": "open_id"
            })
            logger.info(f"Found daily report recipient: {recorder_profile.name} ({recorder_profile.department}) with open_id: {recorder_profile.feishu_open_id}")
        else:
            logger.warning(f"Recorder {recorder_name} (profile: {recorder_profile.name}) has no feishu_open_id, cannot send daily report")
        
        return recipients
    
    # 发送拜访记录通知 - 实时推送给记录人、直属上级、部门负责人
    def send_visit_record_notification(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None,
        visit_record: Dict[str, Any] = None,
        visit_type: str = "form"
    ) -> Dict[str, Any]:
        """
        发送拜访记录通知
        
        支持通过recorder_name或recorder_id查找记录人
        """
        # 获取接收者列表
        recipients = self.get_recipients_for_recorder(
            db_session, 
            recorder_name=recorder_name, 
            recorder_id=recorder_id
        )
        
        if not recipients:
            logger.warning(f"No recipients found for recorder: name={recorder_name}, id={recorder_id}")
            return {
                "success": False,
                "message": "No recipients found",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 准备基础消息内容
        base_template_vars = {
            "visit_date": visit_record.get("visit_communication_date", "--") if visit_record else "--",
            "recorder": recorder_name or "--",
            "department": visit_record.get("department", "--") if visit_record else "--",
            "sales_visit_records": [visit_record] if visit_record else []
        }
        
        # 根据拜访类型和接收者类型确定模板ID
        def get_template_id(recipient_type: str) -> str:
            if visit_type == "form":
                # form类型：销售个人使用新卡片，leader和管理者使用原卡片
                if recipient_type == "recorder":
                    return "AAqzzmP2uT85t"  # 销售个人新卡片
                else:
                    return "AAqz0J0JSTciO"  # leader和管理者原卡片
            else:
                # link类型：使用原有卡片
                return "AAqz0v4nx70HL"
        
        # 获取飞书访问令牌
        try:
            token = get_tenant_access_token(
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET
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
                
                # 根据接收者类型选择模板ID
                template_id = get_template_id(recipient["type"])
                
                # 构建卡片内容
                card_content = {
                    "type": "template",
                    "data": {
                        "template_id": template_id,
                        "template_variable": base_template_vars
                    }
                }
                
                send_feishu_message(
                    recipient["open_id"],
                    token,
                    card_content,
                    receive_id_type=receive_id_type,
                    msg_type="interactive"
                )
                logger.info(
                    f"Successfully pushed visit record to {recipient['name']} "
                    f"({recipient['type']}) in {recipient.get('department', 'Unknown')} "
                    f"using template {template_id}"
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
    
    def send_daily_report_notification(
        self,
        db_session: Session,
        daily_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        发送CRM日报飞书卡片通知
        
        Args:
            db_session: 数据库会话
            daily_report_data: 日报数据，包含recorder、department_name、report_date、statistics等
            
        Returns:
            推送结果信息
        """
        from app.feishu.common_open import get_tenant_access_token, send_feishu_message
        
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
        
        # 获取飞书访问令牌
        try:
            token = get_tenant_access_token(
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET
            )
        except Exception as e:
            logger.error(f"Failed to get tenant access token for daily report: {e}")
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
                    f"Successfully pushed personal daily report to {recipient['name']} "
                    f"in {recipient.get('department', 'Unknown')} department"
                )
                success_count += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to send daily report to {recipient['name']} "
                    f"({recipient['type']}): {str(e)}"
                )
                failed_recipients.append({
                    "name": recipient['name'],
                    "type": recipient['type'],
                    "error": str(e)
                })
        
        logger.info(
            f"Personal daily report for {recorder_name} sent to {success_count}/{len(recipients)} recipient(s)"
        )
        
        return {
            "success": success_count > 0,
            "message": f"Daily report notification sent successfully to {success_count}/{len(recipients)} recipients",
            "recipients_count": len(recipients),
            "success_count": success_count,
            "failed_recipients": failed_recipients
        }
    
    def get_recipients_for_department_report(
        self,
        db_session: Session,
        department_name: str
    ) -> List[Dict[str, Any]]:
        """
        获取部门日报推送的接收者 - 推送给部门负责人
        
        Args:
            db_session: 数据库会话
            department_name: 部门名称
            external: 是否外部推送
            
        Returns:
            接收者列表，包含部门负责人
        """
        recipients = []
        
        # 查找部门负责人
        dept_manager = self.user_profile_repo.get_department_manager(
            db_session, department_name
        )
        
        if dept_manager and dept_manager.feishu_open_id:
            recipients.append({
                "open_id": dept_manager.feishu_open_id,
                "name": dept_manager.name or dept_manager.direct_manager_name,
                "type": "department_manager",
                "department": department_name,
                "receive_id_type": "open_id"
            })
            logger.info(f"Found department manager for {department_name}: {dept_manager.name}")
        else:
            logger.warning(f"No department manager found for department: {department_name}")
        
        return recipients
    
    def send_department_report_notification(
        self,
        db_session: Session,
        department_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        发送部门日报飞书卡片通知
        
        Args:
            db_session: 数据库会话
            department_report_data: 部门日报数据
            external: 是否为外部应用
            
        Returns:
            推送结果信息
        """
        from app.feishu.common_open import get_tenant_access_token, send_feishu_message
        
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
        
        # 获取飞书访问令牌
        try:
            token = get_tenant_access_token(
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET
            )
        except Exception as e:
            logger.error(f"Failed to get tenant access token for department report: {e}")
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
                    f"Successfully pushed department report for {department_name} to {recipient['name']} "
                    f"(department manager)"
                )
                success_count += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to send department report to {recipient['name']}: {str(e)}"
                )
                failed_recipients.append({
                    "name": recipient['name'],
                    "type": recipient['type'],
                    "error": str(e)
                })
        
        logger.info(
            f"Department report for {department_name} sent to {success_count}/{len(recipients)} recipient(s)"
        )
        
        return {
            "success": success_count > 0,
            "message": f"Department report notification sent successfully to {success_count}/{len(recipients)} recipients",
            "recipients_count": len(recipients),
            "success_count": success_count,
            "failed_recipients": failed_recipients
        }
    
    def get_recipients_for_company_report(self) -> List[Dict[str, Any]]:
        """
        获取公司日报推送的接收者 - 根据应用ID判断推送目标
        
        Returns:
            接收者列表，内部环境推送到群聊，外部环境推送给管理员
        """
        from app.feishu.common_open import INTERNAL_APP_IDS, DEFAULT_INTERNAL_GROUP_CHATS
        
        recipients = []
        current_app_id = settings.FEISHU_APP_ID
        
        # 判断是否为内部应用
        is_internal_app = current_app_id in INTERNAL_APP_IDS
        
        if is_internal_app:
            # 内部应用：推送到匹配的群聊
            logger.info(f"当前应用 {current_app_id} 是内部应用，公司日报将推送到群聊")
            
            for group in DEFAULT_INTERNAL_GROUP_CHATS:
                if group["client_id"] == current_app_id:
                    recipients.append({
                        "open_id": group["chat_id"],
                        "name": group["name"],
                        "type": "group_chat",
                        "department": "群聊",
                        "receive_id_type": "chat_id"
                    })
                    logger.info(f"Added company report group chat recipient: {group['name']}")
            
            if not recipients:
                logger.warning(f"内部应用 {current_app_id} 没有找到匹配的群聊配置")
        else:
            # 外部应用：推送给外部管理员
            logger.info(f"当前应用 {current_app_id} 是外部应用，公司日报将推送给外部管理员")
            
            for admin in DEFAULT_EXTERNAL_ADMINS:
                recipients.append({
                    "open_id": admin["open_id"],
                    "name": admin["name"],
                    "type": "external_admin",
                    "department": "管理团队",
                    "receive_id_type": "open_id"
                })
                logger.info(f"Added company report external admin recipient: {admin['name']}")
            
            if not recipients:
                logger.warning("No external admins configured for company report")
        
        return recipients
    
    def send_company_report_notification(
        self,
        db_session: Session,
        company_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        发送公司日报飞书卡片通知
        
        Args:
            db_session: 数据库会话
            company_report_data: 公司日报数据
            external: 是否为外部应用（对公司日报暂时忽略）
            
        Returns:
            推送结果信息
        """
        from app.feishu.common_open import get_tenant_access_token, send_feishu_message
        
        # 获取推送对象 - 根据应用ID判断推送目标
        recipients = self.get_recipients_for_company_report()
        
        if not recipients:
            logger.warning("No recipients found for company report")
            return {
                "success": False,
                "message": "No recipients found for company report",
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
        
        # 获取飞书访问令牌
        try:
            token = get_tenant_access_token(
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET
            )
        except Exception as e:
            logger.error(f"Failed to get tenant access token for company report: {e}")
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
                    f"Successfully pushed company report to {recipient['name']} "
                    f"({recipient['type']})"
                )
                success_count += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to send company report to {recipient['name']}: {str(e)}"
                )
                failed_recipients.append({
                    "name": recipient['name'],
                    "type": recipient['type'],
                    "error": str(e)
                })
        
        logger.info(
            f"Company report sent to {success_count}/{len(recipients)} recipient(s)"
        )
        
        return {
            "success": success_count > 0,
            "message": f"Company report notification sent successfully to {success_count}/{len(recipients)} recipients",
            "recipients_count": len(recipients),
            "success_count": success_count,
            "failed_recipients": failed_recipients
        }
    
    def send_weekly_report_notification(
        self,
        db_session: Session,
        department_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        发送部门周报飞书卡片通知
        
        Args:
            db_session: 数据库会话
            department_report_data: 部门周报数据
            external: 是否为外部应用
            
        Returns:
            推送结果信息
        """
        from app.feishu.common_open import get_tenant_access_token, send_feishu_message
        
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
            department_name=department_name
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
        
        # 获取飞书访问令牌
        try:
            token = get_tenant_access_token(
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET
            )
        except Exception as e:
            logger.error(f"Failed to get tenant access token for weekly report: {e}")
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
                    f"Successfully pushed weekly report to {recipient['name']} "
                    f"({recipient['type']}) for department {department_name}"
                )
                success_count += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to send weekly report to {recipient['name']}: {str(e)}"
                )
                failed_recipients.append({
                    "name": recipient['name'],
                    "type": recipient['type'],
                    "error": str(e)
                })
        
        logger.info(
            f"Weekly report for {department_name} sent to {success_count}/{len(recipients)} recipient(s)"
        )
        
        return {
            "success": success_count > 0,
            "message": f"Weekly report for {department_name} sent to {success_count}/{len(recipients)} recipient(s)",
            "recipients_count": len(recipients),
            "success_count": success_count,
            "failed_recipients": failed_recipients
        }
    
    def get_recipients_for_company_weekly_report(
        self
    ) -> List[Dict[str, Any]]:
        """
        获取公司周报推送的接收者 - 根据应用ID判断推送目标
        
        Returns:
            接收者列表，内部环境推送到群聊，外部环境推送给扩展管理团队
        """
        from app.feishu.common_open import INTERNAL_APP_IDS, DEFAULT_INTERNAL_GROUP_CHATS
        
        recipients = []
        current_app_id = settings.FEISHU_APP_ID
        
        # 判断是否为内部应用
        is_internal_app = current_app_id in INTERNAL_APP_IDS
        
        if is_internal_app:
            # 内部应用：推送到匹配的群聊
            logger.info(f"当前应用 {current_app_id} 是内部应用，公司周报将推送到群聊")
            
            for group in DEFAULT_INTERNAL_GROUP_CHATS:
                if group["client_id"] == current_app_id:
                    recipients.append({
                        "open_id": group["chat_id"],
                        "name": group["name"],
                        "type": "group_chat",
                        "department": "群聊",
                        "receive_id_type": "chat_id"
                    })
                    logger.info(f"Added company weekly report group chat recipient: {group['name']}")
            
            if not recipients:
                logger.warning(f"内部应用 {current_app_id} 没有找到匹配的群聊配置")
        else:
            # 外部应用：推送给扩展管理团队
            logger.info(f"当前应用 {current_app_id} 是外部应用，公司周报将推送给扩展管理团队")
            
            for admin in DEFAULT_EXTERNAL_EXTENDED_ADMINS:
                recipients.append({
                    "open_id": admin["open_id"],
                    "name": admin["name"],
                    "type": "external_extended_admin",
                    "department": "扩展管理团队",
                    "receive_id_type": "open_id"
                })
                logger.info(f"Added company weekly report external extended admin recipient: {admin['name']}")
            
            if not recipients:
                logger.warning("No external extended admins configured for company weekly report")
        
        return recipients
    
    def send_company_weekly_report_notification(
        self,
        company_weekly_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        发送公司周报飞书卡片通知
        
        Args:
            company_weekly_report_data: 公司周报数据
            
        Returns:
            推送结果信息
        """
        from app.feishu.common_open import get_tenant_access_token, send_feishu_message
        
        # 获取推送对象
        recipients = self.get_recipients_for_company_weekly_report()
        
        if not recipients:
            logger.warning("No recipients found for company weekly report")
            return {
                "success": False,
                "message": "No recipients found for company weekly report",
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
        
        # 获取飞书访问令牌
        try:
            token = get_tenant_access_token(
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET
            )
        except Exception as e:
            logger.error(f"Failed to get tenant access token for company weekly report: {e}")
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
                    f"Successfully pushed company weekly report to {recipient['name']} "
                    f"({recipient['type']})"
                )
                success_count += 1
                
            except Exception as e:
                logger.error(
                    f"Failed to send company weekly report to {recipient['name']}: {str(e)}"
                )
                failed_recipients.append({
                    "name": recipient['name'],
                    "type": recipient['type'],
                    "error": str(e)
                })
        
        logger.info(
            f"Company weekly report sent to {success_count}/{len(recipients)} recipient(s)"
        )
        
        return {
            "success": success_count > 0,
            "message": f"Company weekly report sent to {success_count}/{len(recipients)} recipient(s)",
            "recipients_count": len(recipients),
            "success_count": success_count,
            "failed_recipients": failed_recipients
        }
    
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
