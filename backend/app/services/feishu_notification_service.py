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
            logger.info(f"Current app ID {current_app_id} not in internal app list, using first internal app ID: {current_app_id}")
        
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
    
    def get_recipients_for_daily_report(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None,
        external: bool = False
    ) -> List[Dict[str, Any]]:
        """
        获取日报推送的接收者 - 只推送给销售本人
        
        Args:
            db_session: 数据库会话
            recorder_name: 记录人姓名
            recorder_id: 记录人ID
            external: 是否外部推送
            
        Returns:
            接收者列表，只包含销售本人
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
            logger.warning(f"No profile found for daily report recipient: name={recorder_name}, id={recorder_id}")
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
            logger.info(f"Found daily report recipient: {recorder_profile.name} ({recorder_profile.department})")
        else:
            logger.warning(f"Recorder {recorder_name} has no feishu_open_id, cannot send daily report")
        
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
    
    def send_daily_report_notification(
        self,
        db_session: Session,
        daily_report_data: Dict[str, Any],
        external: bool = False
    ) -> Dict[str, Any]:
        """
        发送CRM日报飞书卡片通知
        
        Args:
            db_session: 数据库会话
            daily_report_data: 日报数据，包含recorder、department_name、report_date、statistics等
            external: 是否为外部应用
            
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
        
        # 获取推送对象 - 个人日报只推送给销售本人
        recipients = self.get_recipients_for_daily_report(
            db_session=db_session,
            recorder_name=recorder_name,
            external=external
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
        template_vars = daily_report_data  # 直接使用整个日报数据作为模板变量
        
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
        department_name: str,
        external: bool = False
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
        department_report_data: Dict[str, Any],
        external: bool = False
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
            department_name=department_name,
            external=external
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
        template_vars = {
            **department_report_data,
            'report_date': department_report_data['report_date'].isoformat() if hasattr(department_report_data.get('report_date'), 'isoformat') else str(department_report_data.get('report_date'))
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
    
    def get_recipients_for_company_report(
        self,
        external: bool = False
    ) -> List[Dict[str, Any]]:
        """
        获取公司日报推送的接收者 - 根据应用ID判断推送目标
        
        Args:
            external: 是否外部推送
            
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
        company_report_data: Dict[str, Any],
        external: bool = False
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
        
        # 获取推送对象 - 外部管理员
        recipients = self.get_recipients_for_company_report(external=external)
        
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
        template_vars = {
            **company_report_data,
            'report_date': company_report_data['report_date'].isoformat() if hasattr(company_report_data.get('report_date'), 'isoformat') else str(company_report_data.get('report_date'))
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
