import logging
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple

from uuid import UUID

from app.utils.redis_client import redis_client

from app.platforms.notification_types import (
    NOTIFICATION_TYPE_DAILY_REPORT,
    NOTIFICATION_TYPE_WEEKLY_REPORT,
)
from sqlmodel import Session
from app.repositories.user_profile import user_profile_repo
from app.repositories.department_mirror import department_mirror_repo
from app.platforms.constants import (
    PLATFORM_DINGTALK,
    PLATFORM_FEISHU,
    PLATFORM_LARK,
)
from app.site_settings import SiteSetting
from app.platforms.feishu.client import feishu_client
from app.platforms.lark.client import lark_client
from app.platforms.dingtalk.client import dingtalk_client
from app.core.config import settings
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

class PlatformNotificationService:
    """多平台推送服务 - 基于用户档案进行消息推送，支持飞书和Lark"""

    # Ops backdoor allowlist: only CC company-level daily/weekly reports.
    _OPS_CC_FEISHU_ALLOWED_SOURCES = {"company daily report", "company weekly report"}

    # tenant_access_token 有效期约 2 小时（飞书/钉钉文档），Redis 缓存 110 分钟，多进程/多实例共享
    _TOKEN_CACHE_TTL_SECONDS = 110 * 60

    def _ops_cc_feishu_card(self, card_content: Dict[str, Any], *, source: str) -> None:
        """
        运维后门：用指定的飞书应用，将卡片抄送给指定 open_id 列表。
        - 仅飞书平台
        - 每次事件抄送一次（调用方控制）
        - 失败不影响主流程（仅记录日志）
        """
        try:
            if source not in self._OPS_CC_FEISHU_ALLOWED_SOURCES:
                return
            
            if not settings.OPS_CC_FEISHU_ENABLED:
                return

            open_ids = settings.OPS_CC_FEISHU_OPEN_IDS or []
            if not open_ids:
                return

            app_id = settings.OPS_CC_FEISHU_APP_ID
            app_secret = settings.OPS_CC_FEISHU_APP_SECRET
            if not app_id or not app_secret:
                logger.warning(
                    "Ops CC enabled but missing Feishu app credentials, skip. source=%s",
                    source,
                )
                return

            token = feishu_client.get_tenant_access_token(app_id=app_id, app_secret=app_secret)
            success = 0
            for oid in open_ids:
                try:
                    feishu_client.send_message(
                        oid,
                        token,
                        card_content,
                        receive_id_type="open_id",
                        msg_type="interactive",
                    )
                    success += 1
                except Exception as e:
                    logger.error("Ops CC Feishu send failed. source=%s, open_id=%s, error=%s", source, oid, e)

            logger.info(
                "Ops CC Feishu done. source=%s, success=%s/%s",
                source,
                success,
                len(open_ids),
            )
        except Exception as e:
            logger.error("Ops CC Feishu unexpected error. source=%s, error=%s", source, e)

    def _get_current_app_id(self, platform: Optional[str]) -> Optional[str]:
        """按平台返回当前应用使用的 client_id（用于部门群配置 client_id 过滤）。"""
        if not platform:
            return None
        p = (platform or "").strip().lower()
        if p == PLATFORM_FEISHU:
            return getattr(settings, "FEISHU_APP_ID", None) or None
        if p == PLATFORM_LARK:
            return getattr(settings, "LARK_APP_ID", None) or None
        if p == PLATFORM_DINGTALK:
            return getattr(settings, "DINGTALK_APP_ID", None) or None
        return None

    def _get_department_group_chats_config(self) -> List[Dict[str, Any]]:
        """从站点配置读取部门-群映射（每家客户可独立配置），非 list 或未配置时返回 []。"""
        try:
            val = SiteSetting.get_setting("department_group_chats")
            if isinstance(val, list):
                return val
        except Exception as e:
            logger.debug("department_group_chats not from site settings: %s", e)
        return []

    def _get_group_chats_by_department(
        self,
        department_id: Optional[str] = None,
        department_name: Optional[str] = None,
        notification_type: Optional[str] = None,
        db_session: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        """
        按部门解析要推送的群列表。优先用 department_id 匹配，否则用 department_name。
        只返回当前应用（client_id）可用的群。
        notification_type 用于区分消息类型，仅返回配置了该类型或 "all" 的群：
          "visit_record" - 部门简报群（收文本，部门leader+销售）
          "department_review" - 部门review群（收拜访上级卡片、部门日报卡片、部门周报卡片）
        若配置项含 include_children=true 且传入 db_session，则填写人所在部门为配置部门的任意子部门时也会匹配该群（父部门一个群包住所有子部门）。
        数据来源：站点配置 department_group_chats（每家客户可独立配置）。
        """
        department_group_chats = self._get_department_group_chats_config()
        if not department_group_chats:
            return []
        dept_id = (department_id or "").strip() if department_id else ""
        dept_name = (department_name or "").strip() if department_name else ""
        if not dept_id and not dept_name:
            return []
        if dept_id == "UNKNOWN" and not dept_name:
            return []

        # 用于 include_children：填写人部门的祖先链 id 集合（含自身），仅当提供 db_session 时计算
        ancestor_ids: set = set()
        if db_session:
            if dept_id:
                chains = department_mirror_repo.get_ancestor_chains_bulk(db_session, [dept_id])
                for (aid, _) in chains.get(dept_id, []):
                    ancestor_ids.add(aid)
            elif dept_name:
                resolved_ids = department_mirror_repo.get_department_ids_by_name(db_session, dept_name)
                if resolved_ids:
                    chains = department_mirror_repo.get_ancestor_chains_bulk(db_session, resolved_ids)
                    for did, chain in chains.items():
                        for (aid, _) in chain:
                            ancestor_ids.add(aid)

        seen_key: set = set()
        matching: List[Dict[str, Any]] = []
        for entry in department_group_chats:
            entry_client_id = entry.get("client_id")
            if not entry_client_id:
                continue
            current_app_id = self._get_current_app_id(entry.get("platform"))
            if current_app_id is None or current_app_id != entry_client_id:
                continue
            if notification_type:
                entry_type = (entry.get("notification_type") or "all").strip().lower()
                if entry_type != "all" and entry_type != notification_type.lower():
                    continue
            entry_dept_id = (entry.get("department_id") or "").strip()
            entry_dept_name = (entry.get("department_name") or "").strip()
            include_children = entry.get("include_children") is True

            matched = False
            if include_children and db_session and ancestor_ids and entry_dept_id:
                if entry_dept_id in ancestor_ids:
                    matched = True
            if not matched and dept_id and entry_dept_id == dept_id:
                matched = True
            if not matched and dept_name and entry_dept_name == dept_name:
                matched = True

            if matched:
                key = (entry.get("chat_id"), entry.get("platform"))
                if key not in seen_key:
                    seen_key.add(key)
                    matching.append(dict(entry))
        return matching

    def _send_card_to_group_chats(
        self,
        platform: str,
        group_chats: List[Dict[str, Any]],
        template_id: str,
        template_vars: Dict[str, Any],
        msg_type: str = "interactive",
    ) -> int:
        """
        向群列表发送卡片。每个 chat_id 发一条；失败仅打日志，不抛错。
        返回成功发送的群数量。
        """
        if not group_chats or not template_id:
            return 0
        if not self._validate_platform_support(platform):
            logger.warning("_send_card_to_group_chats: unsupported platform %s", platform)
            return 0
        try:
            token = self._get_tenant_access_token(platform)
        except Exception as e:
            logger.warning("_send_card_to_group_chats: get token failed for %s: %s", platform, e)
            return 0
        card_content = {
            "type": "template",
            "data": {"template_id": template_id, "template_variable": template_vars},
        }
        success = 0
        for group in group_chats:
            chat_id = group.get("chat_id")
            name = group.get("name") or chat_id or "group"
            if not chat_id:
                continue
            try:
                self._send_message(
                    chat_id,
                    token,
                    card_content,
                    platform,
                    receive_id_type="chat_id",
                    msg_type=msg_type,
                )
                success += 1
                logger.info("Sent card to group %s on %s", name, platform)
            except Exception as e:
                logger.warning("Failed to send card to group %s on %s: %s", name, platform, e)
        return success

    def _send_text_to_group_chats(
        self,
        platform: str,
        group_chats: List[Dict[str, Any]],
        text: str,
    ) -> int:
        """
        向群列表发送文本消息。每个 chat_id 发一条；失败仅打日志，不抛错。
        返回成功发送的群数量。
        """
        if not group_chats or not (text or "").strip():
            return 0
        if not self._validate_platform_support(platform):
            logger.warning("_send_text_to_group_chats: unsupported platform %s", platform)
            return 0
        try:
            token = self._get_tenant_access_token(platform)
        except Exception as e:
            logger.warning("_send_text_to_group_chats: get token failed for %s: %s", platform, e)
            return 0
        success = 0
        for group in group_chats:
            chat_id = group.get("chat_id")
            name = group.get("name") or chat_id or "group"
            if not chat_id:
                continue
            try:
                self._send_message(
                    chat_id,
                    token,
                    text,
                    platform,
                    receive_id_type="chat_id",
                    msg_type="text",
                )
                success += 1
                logger.info("Sent text to group %s on %s", name, platform)
            except Exception as e:
                logger.warning("Failed to send text to group %s on %s: %s", name, platform, e)
        return success

    def _format_visit_record_group_message(
        self,
        recorder_name: Optional[str],
        visit_record: Optional[Dict[str, Any]],
    ) -> str:
        """
        格式化拜访记录群推送的文本消息。
        模板：【销售姓名】完成了一次【跟进方式】的客户跟进，并提交了跟进记录。
        """
        rec = visit_record or {}
        sales_name = (recorder_name or rec.get("recorder") or "").strip() or "--"
        method = (rec.get("visit_communication_method") or "").strip() or "--"
        return (
            f"{sales_name}完成了一次{method}的客户跟进，并提交了跟进记录。"
        )
    
    def _get_tenant_access_token(self, platform: str = PLATFORM_FEISHU, external: bool = False) -> str:
        """
        获取指定平台的租户访问令牌。
        优先从 Redis 读取（key: notification:tenant_token:{platform}，TTL 110 分钟）；
        未命中则请求平台 API 并写入 Redis，多进程/多实例共享同一 token。
        """
        token = redis_client.get_tenant_access_token(platform)
        if token:
            return token
        if platform == PLATFORM_FEISHU:
            token = feishu_client.get_tenant_access_token()
        elif platform == PLATFORM_LARK:
            token = lark_client.get_tenant_access_token()
        elif platform == PLATFORM_DINGTALK:
            token = dingtalk_client.get_tenant_access_token()
        else:
            raise ValueError(f"Unsupported platform: {platform}")
        redis_client.set_tenant_access_token(platform, token, self._TOKEN_CACHE_TTL_SECONDS)
        return token
    
    def _send_message(self, open_id: str, token: str, content: Dict[str, Any], platform: str = PLATFORM_FEISHU, receive_id_type: str = "open_id", **kwargs) -> Dict[str, Any]:
        """发送消息到指定平台"""
        if platform == PLATFORM_FEISHU:
            return feishu_client.send_message(open_id, token, content, receive_id_type, **kwargs)
        elif platform == PLATFORM_LARK:
            return lark_client.send_message(open_id, token, content, receive_id_type, **kwargs)
        elif platform == PLATFORM_DINGTALK:
            return dingtalk_client.send_message(open_id, token, content, receive_id_type, **kwargs)
        else:
            raise ValueError(f"Unsupported platform: {platform}")
    
    def _group_recipients_by_platform(self, recipients: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """将接收者列表按平台分组"""
        recipients_by_platform = {}
        for recipient in recipients:
            # 不兜底默认平台：platform 为空/不支持时直接丢弃，并记录日志（便于排查数据源）
            raw_platform = recipient.get("platform")
            platform = (str(raw_platform).strip() if raw_platform is not None else "")
            if not platform:
                logger.warning(
                    "Skip recipient without platform: name=%s type=%s open_id=%s platform=%s",
                    recipient.get("name"),
                    recipient.get("type"),
                    recipient.get("open_id"),
                    raw_platform,
                )
                continue
            if not self._validate_platform_support(platform):
                logger.warning(
                    "Skip recipient with unsupported platform: name=%s type=%s open_id=%s platform=%s",
                    recipient.get("name"),
                    recipient.get("type"),
                    recipient.get("open_id"),
                    platform,
                )
                continue
            if platform not in recipients_by_platform:
                recipients_by_platform[platform] = []
            recipients_by_platform[platform].append(recipient)
        return recipients_by_platform
    
    def _get_platform_tokens(self, platforms: List[str]) -> Dict[str, str]:
        """批量获取多个平台的访问令牌"""
        platform_tokens = {}
        for platform in platforms:
            if not platform:
                continue
            if not self._validate_platform_support(platform):
                continue
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
        return platform in [PLATFORM_FEISHU, PLATFORM_LARK, PLATFORM_DINGTALK]
    
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
                logger.error(f"向{recipient['name']}发送{platform}通知失败: {e}")
                failed_recipients.append(
                    self._create_failed_recipient_record(recipient, platform, str(e))
                )
        
        return success_count, failed_recipients
    
    def _send_notifications_by_platform(
        self,
        recipients_by_platform: Dict[str, List[Dict[str, Any]]],
        card_content: Dict[str, Any] = None,
        card_content_by_platform: Dict[str, Dict[str, Any]] | None = None,
        template_id: str = None,
        template_id_by_platform: Dict[str, str] | None = None,
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

        # Ops backdoor: CC Feishu card once per event (best-effort, no impact on main flow)
        try:
            cc_card_content = (
                (card_content_by_platform or {}).get(PLATFORM_FEISHU) if card_content_by_platform else card_content
            )
            if not cc_card_content and template_vars:
                cc_template_id = (
                    (template_id_by_platform or {}).get(PLATFORM_FEISHU) if template_id_by_platform else template_id
                )
                if cc_template_id:
                    cc_card_content = {
                        "type": "template",
                        "data": {
                            "template_id": cc_template_id,
                            "template_variable": template_vars,
                        },
                    }
            if cc_card_content:
                self._ops_cc_feishu_card(cc_card_content, source=notification_type)
        except Exception as e:
            logger.error("Ops CC Feishu prepare/send failed. source=%s, error=%s", notification_type, e)
        
        # 获取所有需要的平台token
        platforms = [p for p in recipients_by_platform.keys() if p]
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
            platform_card_content = (
                (card_content_by_platform or {}).get(platform) if card_content_by_platform else card_content
            )
            platform_template_id = (
                (template_id_by_platform or {}).get(platform) if template_id_by_platform else template_id
            )
            success_count, failed_recipients = self._send_messages_to_platform(
                platform,
                platform_recipients,
                token,
                platform_card_content,
                platform_template_id,
                template_vars,
            )
            
            total_success_count += success_count
            total_failed_recipients.extend(failed_recipients)
        
        # 统计各平台的结果
        platforms_used = [str(p) for p in recipients_by_platform.keys() if p]
        total_recipients_count = sum(len(recipients) for recipients in recipients_by_platform.values())
        
        return {
            "success": total_success_count > 0,
            "message": f"{notification_type} sent to {total_success_count}/{total_recipients_count} recipients across platforms: {', '.join(platforms_used)}",
            "recipients_count": total_recipients_count,
            "success_count": total_success_count,
            "platforms_used": platforms_used,
            "failed_recipients": total_failed_recipients
        }

    def send_text_notification_to_recipients(
        self,
        *,
        recipients: List[Dict[str, Any]],
        message_text: str,
        notification_type: str = "text notification",
    ) -> Dict[str, Any]:
        """
        向 recipients 列表发送“文本消息”（跨平台），不依赖卡片模板。

        recipients 元素结构沿用本服务内部约定：
        - open_id / platform 必填
        - receive_id_type 可选，默认 open_id
        """
        recipients_by_platform = self._group_recipients_by_platform(recipients or [])
        if not recipients_by_platform:
            return {
                "success": False,
                "message": f"No recipients found for {notification_type}",
                "recipients_count": 0,
                "success_count": 0,
                "platforms_used": [],
                "failed_recipients": [],
            }

        total_success_count = 0
        total_failed_recipients: List[Dict[str, Any]] = []

        platforms = [p for p in recipients_by_platform.keys() if p]
        platform_tokens = self._get_platform_tokens(platforms)

        for platform, platform_recipients in recipients_by_platform.items():
            if not self._validate_platform_support(platform):
                for recipient in platform_recipients:
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(recipient, platform, f"Unsupported platform: {platform}")
                    )
                continue

            token = platform_tokens.get(platform)
            if not token:
                for recipient in platform_recipients:
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(recipient, platform, "Failed to get token")
                    )
                continue

            for recipient in platform_recipients:
                try:
                    receive_id_type = recipient.get("receive_id_type", "open_id")
                    self._send_message(
                        recipient["open_id"],
                        token,
                        message_text,
                        platform,
                        receive_id_type=receive_id_type,
                        msg_type="text",
                    )
                    total_success_count += 1
                except Exception as e:
                    total_failed_recipients.append(
                        self._create_failed_recipient_record(recipient, platform, str(e))
                    )

        platforms_used = [str(p) for p in recipients_by_platform.keys() if p]
        total_recipients_count = sum(len(v) for v in recipients_by_platform.values())
        return {
            "success": total_success_count > 0,
            "message": (
                f"{notification_type} sent to {total_success_count}/{total_recipients_count} "
                f"recipients across platforms: {', '.join(platforms_used)}"
            ),
            "recipients_count": total_recipients_count,
            "success_count": total_success_count,
            "platforms_used": platforms_used,
            "failed_recipients": total_failed_recipients,
        }
    
    def _get_reporting_chain_leaders(
        self,
        base_user_id: str,
        max_levels: int = 2,
        include_leader_identity: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        调用 OAuth 服务查询用户的汇报链，并返回精简后的领导信息列表
        """
        if not base_user_id:
            return []

        return oauth_client.get_reporting_chain_leaders(
            base_user_id=base_user_id,
            max_levels=max_levels,
            include_leader_identity=include_leader_identity,
        )
    
    def _get_card_permission_receivers(self, permission: str, role_codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        调用 OAuth 服务查询具有指定权限和角色代码的用户列表
        
        对应接口：
        POST /permission/users/by-permission
        {
            "permission": permission,
            "roleCodes": role_codes,
            "includeIdentity": true
        }
        """
        return oauth_client.get_users_by_permission(
            permission=permission,
            role_codes=role_codes,
            include_identity=True,
        )
    
    def get_recipients_for_recorder(
        self, 
        db_session: Session, 
        recorder_name: str = None,
        recorder_id: str = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        获取记录人相关的推送接收者，按平台分组
        包括：记录人本人 + OAuth 汇报链中的所有上级（汇报链已含直属上级）
        
        支持通过recorder_name或recorder_id查找
        返回按平台分组的接收者字典
        """
        recipients_by_platform: Dict[str, List[Dict[str, Any]]] = {}
        
        # 1. 查找记录人的档案
        recorder_profile = None
        
        if recorder_id:
            # 优先使用recorder_id查找
            recorder_profile = user_profile_repo.get_by_recorder_id(db_session, recorder_id)
        
        if not recorder_profile and recorder_name:
            # 如果recorder_id没找到，再尝试通过姓名查找
            recorder_profile = user_profile_repo.get_by_name(db_session, recorder_name)
        
        if not recorder_profile:
            logger.warning(f"No profile found for recorder: name={recorder_name}, id={recorder_id}")
            return recipients_by_platform
        
        # 2. 获取记录人的 OAuth 账号
        oauth_account = recorder_profile.oauth_user
        if not oauth_account:
            logger.warning(
                f"Recorder {recorder_name} (profile: {recorder_profile.name}) has no oauth_user, "
                f"cannot resolve notification recipients via reporting chain"
            )
            return recipients_by_platform
        
        # 3. 先添加记录人本人
        platform = oauth_account.provider
        if not self._validate_platform_support(platform):
            logger.warning(f"Recorder platform {platform} not supported, skipping recorder")
        else:
            recorder_open_id = oauth_account.open_id
            if not recorder_open_id:
                logger.warning(
                    f"Recorder {recorder_name} (profile: {recorder_profile.name}) "
                    f"has no open_id, cannot send notification"
                )
            else:
                if platform not in recipients_by_platform:
                    recipients_by_platform[platform] = []
                
                recipients_by_platform[platform].append(
                    {
                        "open_id": recorder_open_id,
                        "name": recorder_profile.name or recorder_name or "Unknown",
                        "type": "recorder",
                        "department": recorder_profile.department,
                        "receive_id_type": "open_id",
                        "platform": platform,
                    }
                )
        
        # 4. 调用 OAuth 服务查询汇报链领导（汇报链已含直属上级）
        # 使用系统用户ID（user_id），而不是OAuth平台的用户ID
        base_user_id = None
        if recorder_profile.user_id:
            # 将UUID转换为字符串
            base_user_id = str(recorder_profile.user_id)
        elif oauth_account.user_id:
            # 如果profile没有user_id，尝试从oauth_account获取
            base_user_id = str(oauth_account.user_id)
        
        if not base_user_id:
            logger.warning(
                f"Recorder {recorder_name} (profile: {recorder_profile.name}) "
                f"has no system user_id for reporting-chain query"
            )
            return recipients_by_platform
        
        leaders = self._get_reporting_chain_leaders(base_user_id)
        if not leaders:
            logger.info(
                f"No leaders found from reporting chain for recorder: "
                f"name={recorder_name}, id={recorder_id}, base_user_id={base_user_id}"
            )
            return recipients_by_platform
        
        # 5. 将汇报链领导加入接收者列表，按平台分组并去重
        for leader in leaders:
            platform = leader.get("platform")
            if not platform:
                continue
            
            if not self._validate_platform_support(platform):
                logger.warning(f"Leader platform {platform} not supported, skipping")
                continue
            
            open_id = leader.get("open_id")
            if not open_id:
                logger.warning(f"Leader missing open_id after normalization: {leader}")
                continue
            
            if platform not in recipients_by_platform:
                recipients_by_platform[platform] = []
            
            existing_open_ids = {r["open_id"] for r in recipients_by_platform[platform]}
            if open_id in existing_open_ids:
                # 避免重复推送（例如某些领导已在其他逻辑中添加）
                continue
            
            recipients_by_platform[platform].append(
                {
                    "open_id": open_id,
                    "name": leader.get("name") or "Unknown",
                    "type": "leader",
                    "department": leader.get("department") or "部门团队",
                    "receive_id_type": "open_id",
                    "platform": platform,
                }
            )
        
        # 6. 添加单独配置了“卡片接收权限”的用户（通常为公司高层或管理员），并与现有集合去重
        card_receivers = self._get_card_permission_receivers("visit_record:card:receive")
        for user in card_receivers:
            platform = user.get("platform")
            if not platform:
                continue

            if not self._validate_platform_support(platform):
                logger.warning(f"Card-permission user platform {platform} not supported, skipping")
                continue

            open_id = user.get("open_id")
            if not open_id:
                logger.warning(f"Card-permission user missing open_id: {user}")
                continue

            if platform not in recipients_by_platform:
                recipients_by_platform[platform] = []

            existing_open_ids = {r["open_id"] for r in recipients_by_platform[platform]}
            if open_id in existing_open_ids:
                # 已经在记录人或汇报链中，避免重复推送
                continue

            recipients_by_platform[platform].append(
                {
                    "open_id": open_id,
                    "name": user.get("name") or "Unknown",
                    "type": "executive_admin",
                    "department": user.get("department") or "公司",
                    "receive_id_type": "open_id",
                    "platform": platform,
                }
            )
        
        return recipients_by_platform
    
    def get_recipients_for_sales_daily_report(
        self,
        db_session: Session,
        recorder_name: str = None,
        recorder_id: str = None,
        department_name: str = None
    ) -> List[Dict[str, Any]]:
        """获取销售个人日报推送的接收者 - 只推送给销售本人"""
        
        recipients = []
        
        # 1. 查找记录人的档案
        recorder_profile = None
        
        if recorder_id:
            # 优先使用recorder_id查找
            recorder_profile = user_profile_repo.get_by_recorder_id(db_session, recorder_id)
            if recorder_profile:
                logger.info(f"Found profile by recorder_id: {recorder_id} -> {recorder_profile.name}")
        
        if not recorder_profile and recorder_name:
            # 如果recorder_id没找到，使用姓名和部门组合查找（更精确）
            if department_name:
                recorder_profile = user_profile_repo.get_by_name_and_department(
                    db_session, recorder_name, department_name
                )
                if recorder_profile:
                    logger.info(f"Found profile by name+department: {recorder_name} in {department_name} -> {recorder_profile.name}")
            else:
                # 如果没有部门信息，只按姓名查找
                recorder_profile = user_profile_repo.get_by_name(db_session, recorder_name)
                if recorder_profile:
                    logger.info(f"Found profile by name only: {recorder_name} -> {recorder_profile.name}")
        
        if not recorder_profile:
            logger.warning(f"No profile found for daily report recipient: name={recorder_name}, id={recorder_id}, department={department_name}")
            # 记录详细信息以便调试
            logger.warning(f"Available profiles with similar names:")
            try:
                all_profiles = user_profile_repo.get_all_active_profiles(db_session)
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
        recorder_open_id = recorder_profile.oauth_user.open_id
        if recorder_open_id:
            recipients.append({
                "open_id": recorder_open_id,
                "name": recorder_profile.name or recorder_name or "Unknown",
                "type": "recorder",
                "department": recorder_profile.department,
                "receive_id_type": "open_id",
                "platform": recorder_profile.oauth_user.provider
            })
            logger.info(f"Found daily report recipient: {recorder_profile.name} ({recorder_profile.department}) with {recorder_profile.oauth_user.provider} open_id: {recorder_open_id}")
        else:
            logger.warning(f"Recorder {recorder_name} (profile: {recorder_profile.name}) has no {recorder_profile.oauth_user.provider} open_id, cannot send daily report")
        
        return recipients

    def _collect_visit_record_recipients_and_groups(
        self,
        db_session: Session,
        recorder_name: Optional[str],
        recorder_id: Optional[str],
        visit_record: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        汇总拜访记录推送的接收者与部门群配置。
        若配置了 department_review 群，则从个人接收者中移除 leader/管理层，仅保留记录人与协同人。
        返回 (recipients_by_platform, department_groups_review, department_groups_brief)。
        """
        recipients_by_platform = self.get_recipients_for_recorder(
            db_session, recorder_name=recorder_name, recorder_id=recorder_id
        )
        collaborative = self._get_collaborative_participants_recipients(db_session, visit_record)
        if collaborative:
            for platform, recipients in collaborative.items():
                if platform in recipients_by_platform:
                    recipients_by_platform[platform].extend(recipients)
                else:
                    recipients_by_platform[platform] = recipients

        recorder_dept_id = (visit_record or {}).get("recorder_department_id")
        recorder_dept_name = (visit_record or {}).get("recorder_department_name")
        department_groups_review = self._get_group_chats_by_department(
            department_id=recorder_dept_id,
            department_name=recorder_dept_name,
            notification_type="department_review",
            db_session=db_session,
        )
        department_groups_brief = self._get_group_chats_by_department(
            department_id=recorder_dept_id,
            department_name=recorder_dept_name,
            notification_type="visit_record",
            db_session=db_session,
        )

        if department_groups_review:
            for platform in list(recipients_by_platform.keys()):
                recipients_by_platform[platform] = [
                    r for r in recipients_by_platform[platform]
                    if r.get("type") in ("recorder", "collaborative_participant")
                ]
            recipients_by_platform = {p: rs for p, rs in recipients_by_platform.items() if rs}

        return recipients_by_platform, department_groups_review, department_groups_brief

    def _prepare_visit_record_template_vars(
        self,
        record_id: str,
        recorder_name: Optional[str],
        visit_record: Optional[Dict[str, Any]],
        meeting_notes: Optional[str],
        risk_info: Optional[str],
    ) -> Dict[str, Any]:
        """
        准备拜访记录卡片/文案的公共模板变量；会原地格式化 visit_record 中的协同人、动态字段等。
        """
        if visit_record:
            from app.utils.participants_utils import format_collaborative_participants_names
            text = format_collaborative_participants_names(visit_record.get("collaborative_participants")) or "--"
            visit_record["collaborative_participants"] = text
            logger.info("Replaced collaborative participants: %s -> %s", visit_record.get("collaborative_participants"), text)

        dynamic_fields = []
        if visit_record:
            from app.crm.save_engine import generate_dynamic_fields_for_visit_record
            dynamic_fields = generate_dynamic_fields_for_visit_record(visit_record)

        return {
            "visit_date": (visit_record or {}).get("last_modified_time", "--"),
            "recorder": recorder_name or "--",
            "department": (visit_record or {}).get("department", "--"),
            "sales_visit_records": [visit_record] if visit_record else [],
            "meeting_notes": meeting_notes,
            "risk_info": risk_info or "--",
            "dynamic_fields": dynamic_fields,
            "comment_page_url": f"{settings.REVIEW_REPORT_HOST}/registerVisitRecord/addComment?record_id={record_id}",
        }

    def _get_visit_record_template_id(
        self,
        recipient_type: str,
        platform: str,
        visit_type: str,
        form_type: Optional[str] = None,
    ) -> Optional[str]:
        """按接收者类型、平台、拜访类型与表单类型返回拜访记录卡片模板 ID。"""
        if platform not in (PLATFORM_FEISHU, PLATFORM_LARK, PLATFORM_DINGTALK):
            logger.warning("Unsupported platform: %s", platform)
            return None
        if platform == PLATFORM_DINGTALK:
            if visit_type == "form":
                if recipient_type in ("recorder", "collaborative_participant"):
                    return "ceda714f-6862-4f42-a77f-7f6d6f95f06d.schema"
                return "1ea96d75-f14a-4dbc-87e5-baf3f893f5b5.schema"
            return "28dd4d85-7f38-4a5c-9bdb-8156bdff4d20.schema"
        if platform in (PLATFORM_FEISHU, PLATFORM_LARK):
            if visit_type == "form":
                form_type = form_type or settings.CRM_VISIT_RECORD_FORM_TYPE.value
                if form_type == "simple":
                    if recipient_type in ("recorder", "collaborative_participant"):
                        return "AAqzQK6iUiK2k"
                    return "AAqzQKvKzOW1z"
                if recipient_type in ("recorder", "collaborative_participant"):
                    return "AAqv2BVqurMLn"
                return "AAqv2BIB41oor"
            return "AAqv2BCd4MmZW"
        return None

    def _send_visit_record_to_individual_recipients(
        self,
        recipients_by_platform: Dict[str, List[Dict[str, Any]]],
        base_template_vars: Dict[str, Any],
        visit_type: str,
        visit_record: Optional[Dict[str, Any]],
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """向个人接收者发送拜访记录卡片，返回 (成功数, 失败列表)。"""
        if not recipients_by_platform:
            return 0, []
        platforms = [p for p in recipients_by_platform.keys() if p]
        platform_tokens = self._get_platform_tokens(platforms)
        success_count = 0
        failed_recipients: List[Dict[str, Any]] = []
        form_type = (visit_record or {}).get("form_type") if visit_record else None

        for platform, platform_recipients in recipients_by_platform.items():
            if not self._validate_platform_support(platform):
                for r in platform_recipients:
                    failed_recipients.append(self._create_failed_recipient_record(r, platform, f"Unsupported platform: {platform}"))
                continue
            if platform not in platform_tokens:
                for r in platform_recipients:
                    failed_recipients.append(self._create_failed_recipient_record(r, platform, "Failed to get token"))
                continue

            token = platform_tokens[platform]
            for recipient in platform_recipients:
                template_id = self._get_visit_record_template_id(recipient["type"], platform, visit_type, form_type)
                if not template_id:
                    failed_recipients.append(
                        self._create_failed_recipient_record(recipient, platform, "No template available for platform")
                    )
                    continue
                card_content = {"type": "template", "data": {"template_id": template_id, "template_variable": base_template_vars}}
                try:
                    self._send_message(
                        recipient["open_id"],
                        token,
                        card_content,
                        platform,
                        receive_id_type=recipient.get("receive_id_type", "open_id"),
                        msg_type="interactive",
                    )
                    success_count += 1
                    logger.info(
                        "Pushed visit record to %s (%s) on %s",
                        recipient.get("name"), recipient.get("type"), platform,
                    )
                except Exception as e:
                    logger.error("Failed to push visit record to %s on %s: %s", recipient.get("name"), platform, e)
                    failed_recipients.append(self._create_failed_recipient_record(recipient, platform, str(e)))

        return success_count, failed_recipients

    def _send_visit_record_to_review_groups(
        self,
        department_groups_review: List[Dict[str, Any]],
        base_template_vars: Dict[str, Any],
        visit_type: str,
        visit_record: Optional[Dict[str, Any]],
    ) -> None:
        """将上级/管理层卡片推送到部门 review 群。"""
        if not department_groups_review:
            return
        by_platform = defaultdict(list)
        for g in department_groups_review:
            p = g.get("platform")
            if p:
                by_platform[p].append(g)
        form_type = (visit_record or {}).get("form_type") if visit_record else None
        for platform, group_chats in by_platform.items():
            if not self._validate_platform_support(platform):
                continue
            template_id = self._get_visit_record_template_id("leader", platform, visit_type, form_type)
            if template_id:
                n = self._send_card_to_group_chats(
                    platform=platform,
                    group_chats=group_chats,
                    template_id=template_id,
                    template_vars=base_template_vars,
                    msg_type="interactive",
                )
                if n:
                    logger.info("Visit record review group push: sent leader card to %s groups on %s", n, platform)

    def _send_visit_record_to_brief_groups(
        self,
        department_groups_brief: List[Dict[str, Any]],
        recorder_name: Optional[str],
        visit_record: Optional[Dict[str, Any]],
    ) -> None:
        """向部门简报群发送拜访记录文本。"""
        if not department_groups_brief:
            return
        by_platform = defaultdict(list)
        for g in department_groups_brief:
            p = g.get("platform")
            if p:
                by_platform[p].append(g)
        message_text = self._format_visit_record_group_message(recorder_name, visit_record)
        for platform, group_chats in by_platform.items():
            if not self._validate_platform_support(platform):
                continue
            n = self._send_text_to_group_chats(platform=platform, group_chats=group_chats, text=message_text)
            if n:
                logger.info("Visit record group push: sent text to %s groups on %s", n, platform)

    # 发送拜访记录通知 - 实时推送给记录人、直属上级、部门负责人
    def send_visit_record_notification(
        self,
        db_session: Session,
        record_id: str,
        recorder_name: str = None,
        recorder_id: str = None,
        visit_record: Dict[str, Any] = None,
        visit_type: str = "form",
        meeting_notes: str = None,
        risk_info: str = None
    ) -> Dict[str, Any]:
        """
        发送拜访记录通知。
        支持通过 recorder_name 或 recorder_id 查找记录人；link 类型会包含会议纪要总结。
        """
        recipients_by_platform, department_groups_review, department_groups_brief = (
            self._collect_visit_record_recipients_and_groups(
                db_session, recorder_name, recorder_id, visit_record
            )
        )
        base_template_vars = self._prepare_visit_record_template_vars(
            record_id, recorder_name, visit_record, meeting_notes, risk_info
        )

        if not recipients_by_platform and not department_groups_review and not department_groups_brief:
            logger.warning(
                "No recipients and no department groups for recorder: name=%s, id=%s",
                recorder_name, recorder_id,
            )
            return {
                "success": False,
                "message": "No recipients found",
                "recipients_count": 0,
                "success_count": 0,
            }

        total_success_count, total_failed_recipients = self._send_visit_record_to_individual_recipients(
            recipients_by_platform, base_template_vars, visit_type, visit_record
        )
        self._send_visit_record_to_review_groups(
            department_groups_review, base_template_vars, visit_type, visit_record
        )
        self._send_visit_record_to_brief_groups(
            department_groups_brief, recorder_name, visit_record
        )

        platforms_used = [str(p) for p in recipients_by_platform.keys() if p]
        total_recipients_count = sum(len(rs) for rs in recipients_by_platform.values())
        result = {
            "success": total_success_count > 0,
            "message": f"Pushed to {total_success_count}/{total_recipients_count} recipients across platforms: {', '.join(platforms_used)}",
            "recipients_count": total_recipients_count,
            "success_count": total_success_count,
            "platforms_used": platforms_used,
            "failed_recipients": total_failed_recipients,
        }
        logger.info("Visit record notification result: %s", result)
        return result

    def send_sales_daily_report_notification(
        self,
        db_session: Session,
        daily_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送CRM销售个人日报飞书卡片通知"""
        
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
        recipients = self.get_recipients_for_sales_daily_report(
            db_session=db_session,
            recorder_name=recorder_name,
            recorder_id=recorder_id,
            department_name=department_name
        )
        
        if not recipients:
            logger.warning(f"No recipients found for sales daily report of {recorder_name}")
            return {
                "success": False,
                "message": f"No recipients found for sales daily report of {recorder_name}",
                "recipients_count": 0,
                "success_count": 0
            }
        
        template_vars = self._convert_daily_report_data_for_feishu(db_session, daily_report_data)
        # 个人日报卡片模板
        template_id_by_platform = {
            PLATFORM_DINGTALK: "40452d31-c1fa-46b3-b0ea-28921bcf52ae.schema",
            PLATFORM_FEISHU: "AAqvGwEs503C4",
            PLATFORM_LARK: "AAqvGwEs503C4",
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            template_id_by_platform=template_id_by_platform,
            template_vars=template_vars,
            notification_type="sales daily report"
        )
    
    def get_recipients_for_department_report_from_profile(
        self,
        db_session: Session,
        department_name: str
    ) -> List[Dict[str, Any]]:
        """获取部门日报/周报推送的接收者 - 从profile中获取部门负责人"""
        recipients = []
        
        # 查找部门负责人
        dept_manager = user_profile_repo.get_department_manager(
            db_session, department_name
        )
        
        if dept_manager:
            dept_manager_open_id = dept_manager.oauth_user.open_id
            if dept_manager_open_id:
                recipients.append({
                    "open_id": dept_manager_open_id,
                    "name": dept_manager.name or dept_manager.direct_manager_name,
                    "type": "department_manager",
                    "department": department_name,
                    "receive_id_type": "open_id",
                    "platform": dept_manager.oauth_user.provider
                })
                logger.info(f"Found department manager for {department_name} on {dept_manager.oauth_user.provider}: {dept_manager.name}")
            else:
                logger.warning(f"Department manager {dept_manager.name} has no {dept_manager.oauth_user.provider} open_id")
        else:
            logger.warning(f"No department manager found for department: {department_name}")
        
        return recipients

    def _send_report_to_department_review_groups_or_recipients(
        self,
        db_session: Session,
        department_name: str,
        recipients: Optional[List[Dict[str, Any]]],
        template_id_by_platform: Dict[str, str],
        template_vars: Dict[str, Any],
        notification_type: str,
        report_kind: str = "department report",
    ) -> Dict[str, Any]:
        """
        部门类报告推送：优先推送到 department_review 群；若未配置群则推送给个人接收者。
        notification_type 用于 _send_notifications_by_platform 的日志；report_kind 用于无接收人时的错误文案。
        """
        department_groups_review = self._get_group_chats_by_department(
            department_id=None,
            department_name=department_name,
            notification_type="department_review",
            db_session=db_session,
        )
        if not recipients and not department_groups_review:
            logger.warning(
                "No recipients and no review group for %s of %s",
                report_kind, department_name,
            )
            return {
                "success": False,
                "message": f"No recipients found for {report_kind} of {department_name}",
                "recipients_count": 0,
                "success_count": 0,
            }
        if department_groups_review:
            by_platform = defaultdict(list)
            for g in department_groups_review:
                p = g.get("platform")
                if p:
                    by_platform[p].append(g)
            success_count = 0
            for platform, group_chats in by_platform.items():
                if not self._validate_platform_support(platform):
                    continue
                template_id = template_id_by_platform.get(platform)
                if template_id:
                    n = self._send_card_to_group_chats(
                        platform=platform,
                        group_chats=group_chats,
                        template_id=template_id,
                        template_vars=template_vars,
                        msg_type="interactive",
                    )
                    success_count += n
            return {
                "success": success_count > 0,
                "message": f"Pushed {report_kind} to {success_count} review group(s)" if success_count else "No review groups sent",
                "recipients_count": success_count,
                "success_count": success_count,
            }
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            template_id_by_platform=template_id_by_platform,
            template_vars=template_vars,
            notification_type=notification_type,
        )

    def send_department_daily_report_notification(
        self,
        db_session: Session,
        department_report_data: Dict[str, Any],
        recipients: Optional[List[Dict[str, Any]]] = None
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
        if not recipients:
            recipients = self.get_recipients_for_department_report_from_profile(
                db_session=db_session,
                department_name=department_name
            )
        template_vars = self._convert_daily_report_data_for_feishu(db_session, department_report_data)
        if "report_date" in template_vars and hasattr(template_vars["report_date"], "isoformat"):
            template_vars["report_date"] = template_vars["report_date"].isoformat()
        template_id_by_platform = {
            PLATFORM_DINGTALK: "caae8019-62c5-4f3d-9387-0616b365039b.schema",
            PLATFORM_FEISHU: "AAqvGxezuuhGD",
            PLATFORM_LARK: "AAqvGxezuuhGD",
        }
        return self._send_report_to_department_review_groups_or_recipients(
            db_session=db_session,
            department_name=department_name,
            recipients=recipients,
            template_id_by_platform=template_id_by_platform,
            template_vars=template_vars,
            notification_type="department report",
            report_kind="department daily report",
        )
    
    def get_recipients_for_company_daily_report(self, db_session: Session) -> List[Dict[str, Any]]:
        """
        获取公司日报推送的接收者
        
        规则：
        - 调用 OAuth 权限服务，查询拥有
          permission = "daily_report:company:card:receive"
          的用户作为接收人
        - 如果未查询到，则从profile中获取可以接收公司日报的人员（向后兼容）
        """
        recipients: List[Dict[str, Any]] = []
        
        card_receivers = self._get_card_permission_receivers(
            permission="daily_report:company:card:receive",
        )
        
        for user in card_receivers:
            platform = user.get("platform")
            open_id = user.get("open_id")
            name = user.get("name") or "Unknown"
            
            if not platform or not open_id:
                logger.warning(f"Skip company-report card-permission user without platform/open_id: {user}")
                continue
            
            if not self._validate_platform_support(platform):
                logger.warning(f"Company-report user platform {platform} not supported, skipping")
                continue
            
            recipients.append(
                {
                    "open_id": open_id,
                    "name": name,
                    "type": "company_executive",
                    "receive_id_type": "open_id",
                    "platform": platform,
                }
            )
            logger.info(
                f"Added company report recipient from card-permission: {name} "
                f"on {platform}"
            )
        
        if not recipients:
            logger.warning(
                "No recipients found for company daily report via card-permission "
                '(permission="daily_report:company:card:receive")'
            )
            
            # 从profile中获取可以接收公司日报的人员（向后兼容）
            profiles = user_profile_repo.get_users_by_notification_permission(db_session, NOTIFICATION_TYPE_DAILY_REPORT)
            
            for profile in profiles:
                profile_open_id = profile.oauth_user.open_id
                if profile_open_id:
                    recipients.append({
                        "open_id": profile_open_id,
                        "name": profile.name,
                        "type": "company_executive",
                        "receive_id_type": "open_id",
                        "platform": profile.oauth_user.provider
                    })
                    logger.info(f"Added company daily report recipient from profile: {profile.name} on {profile.oauth_user.provider}")
        
        return recipients
    
    def send_company_daily_report_notification(
        self,
        db_session: Session,
        company_report_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送公司日报飞书卡片通知"""
        
        # 获取推送对象 - 根据应用ID判断推送目标
        recipients = self.get_recipients_for_company_daily_report(db_session)
        
        if not recipients:
            logger.warning(f"No recipients found for company daily report")
            return {
                "success": False,
                "message": f"No recipients found for company daily report",
                "recipients_count": 0,
                "success_count": 0
            }
        
        template_vars = self._convert_daily_report_data_for_feishu(db_session, company_report_data)
        # 确保日期字段是字符串格式
        if 'report_date' in template_vars and hasattr(template_vars['report_date'], 'isoformat'):
            template_vars['report_date'] = template_vars['report_date'].isoformat()
        # 公司日报卡片模板
        template_id_by_platform = {
            PLATFORM_DINGTALK: "6618eed4-1d1a-4536-9625-61e99cb14837.schema",
            PLATFORM_FEISHU: "AAqvGhJJNR59v",
            PLATFORM_LARK: "AAqvGhJJNR59v",
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            template_id_by_platform=template_id_by_platform,
            template_vars=template_vars,
            notification_type="company daily report"
        )
    
    def send_weekly_report_notification(
        self,
        db_session: Session,
        department_report_data: Dict[str, Any],
        recipients: Optional[List[Dict[str, Any]]] = None,
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
        if not recipients:
            recipients = self.get_recipients_for_department_report_from_profile(
                db_session=db_session,
                department_name=department_name,
            )
        template_vars = self._convert_weekly_report_data_for_feishu(db_session, department_report_data)
        template_id_by_platform = {
            PLATFORM_DINGTALK: settings.DINGTALK_DEPT_WEEKLY_REPORT_TEMPLATE_ID,
            PLATFORM_FEISHU: settings.FEISHU_DEPT_WEEKLY_REPORT_TEMPLATE_ID,
            PLATFORM_LARK: settings.FEISHU_DEPT_WEEKLY_REPORT_TEMPLATE_ID,
        }
        return self._send_report_to_department_review_groups_or_recipients(
            db_session=db_session,
            department_name=department_name,
            recipients=recipients,
            template_id_by_platform=template_id_by_platform,
            template_vars=template_vars,
            notification_type="weekly report",
            report_kind="department weekly report",
        )
    
    def get_recipients_for_company_weekly_report(
        self,
        db_session: Session
    ) -> List[Dict[str, Any]]:
        """
        获取公司周报推送的接收者

        规则：
        - 调用 OAuth 权限服务，查询拥有
          permission = "weekly_report:company:card:receive"
          的用户作为接收人
        - 如果未查询到，则从profile中获取可以接收公司周报的人员（向后兼容）
        """

        recipients: List[Dict[str, Any]] = []

        card_receivers = self._get_card_permission_receivers(
            permission="weekly_report:company:card:receive",
        )

        for user in card_receivers:
            platform = user.get("platform")
            open_id = user.get("open_id")
            name = user.get("name") or "Unknown"

            if not platform or not open_id:
                logger.warning(f"Skip company-weekly-report card-permission user without platform/open_id: {user}")
                continue

            if not self._validate_platform_support(platform):
                logger.warning(f"Company-weekly-report user platform {platform} not supported, skipping")
                continue

            recipients.append(
                {
                    "open_id": open_id,
                    "name": name,
                    "type": "weekly_report_recipient",
                    "department": "管理团队",
                    "receive_id_type": "open_id",
                    "platform": platform,
                }
            )
            logger.info(
                f"Added company weekly report recipient from card-permission: {name} "
                f"on {platform}"
            )

        if not recipients:
            logger.warning(
                "No recipients found for company weekly report via card-permission "
                '(permission="weekly_report:company:card:receive")'
            )

            # 从profile中获取可以接收周报的人员（向后兼容）
            profiles = user_profile_repo.get_users_by_notification_permission(
                db_session, NOTIFICATION_TYPE_WEEKLY_REPORT
            )

            for profile in profiles:
                profile_open_id = profile.oauth_user.open_id
                if profile_open_id:
                    recipients.append(
                        {
                            "open_id": profile_open_id,
                            "name": profile.name,
                            "type": "weekly_report_recipient",
                            "department": profile.department or "管理团队",
                            "receive_id_type": "open_id",
                            "platform": profile.oauth_user.provider,
                        }
                    )
                    logger.info(
                        f"Added company weekly report recipient from profile: {profile.name} "
                        f"on {profile.oauth_user.provider}"
                    )

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
        
        template_vars = self._convert_weekly_report_data_for_feishu(db_session, company_weekly_report_data)
        # 公司周报卡片模板 - 从配置文件读取，支持不同公司使用不同的模板
        template_id_by_platform = {
            PLATFORM_DINGTALK: settings.DINGTALK_COMPANY_WEEKLY_REPORT_TEMPLATE_ID,
            PLATFORM_FEISHU: settings.FEISHU_COMPANY_WEEKLY_REPORT_TEMPLATE_ID,
            PLATFORM_LARK: settings.FEISHU_COMPANY_WEEKLY_REPORT_TEMPLATE_ID,
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(all_recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            template_id_by_platform=template_id_by_platform,
            template_vars=template_vars,
            notification_type="company weekly report"
        )

    def send_weekly_followup_comment_notification(
        self,
        db_session: Session,
        *,
        recipient_user_id: str,
        message_text: str,
    ) -> Dict[str, Any]:
        """
        周跟进总结评论提醒：给负责销售发一条「文本消息」。
        - 不走卡片模板（避免引入新模板依赖）
        - 失败不应影响主业务流程，调用方可自行 try/except
        """
        try:
            user_uuid = UUID(str(recipient_user_id))
        except Exception:
            return {"success": False, "message": "invalid recipient_user_id", "recipients_count": 0, "success_count": 0}

        profile = user_profile_repo.get_by_user_id(db_session, user_uuid)
        if not profile or not profile.oauth_user or not profile.oauth_user.open_id or not profile.oauth_user.provider:
            return {"success": False, "message": "recipient has no oauth open_id", "recipients_count": 0, "success_count": 0}

        platform = profile.oauth_user.provider
        open_id = profile.oauth_user.open_id
        token = self._get_tenant_access_token(platform)

        # 发送文本消息
        self._send_message(open_id, token, message_text, platform, receive_id_type="open_id", msg_type="text")
        return {"success": True, "message": "ok", "recipients_count": 1, "success_count": 1}

    def send_visit_record_comment_notification(
        self,
        db_session: Session,
        *,
        recipient_user_id: str,
        message_text: str,
    ) -> Dict[str, Any]:
        """
        拜访记录评论提醒：给记录人发一条「文本消息」。
        复用 send_weekly_followup_comment_notification 的通用发送逻辑。
        """
        return self.send_weekly_followup_comment_notification(
            db_session,
            recipient_user_id=recipient_user_id,
            message_text=message_text,
        )
    
    def send_sales_task_created_notification(
        self,
        db_session: Session,
        *,
        recipient_user_id: str,
        message_text: str,
    ) -> Dict[str, Any]:
        """
        销售任务创建提醒：给任务负责人发一条「文本消息」。
        复用 send_weekly_followup_comment_notification 的通用发送逻辑。
        """
        return self.send_weekly_followup_comment_notification(
            db_session,
            recipient_user_id=recipient_user_id,
            message_text=message_text,
        )
    
    def _convert_weekly_report_data_for_feishu(self, db_session: Session, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将周报数据转换为飞书卡片所需的格式（所有数值和日期转换为字符串）
        
        Args:
            db_session: 数据库会话
            report_data: 原始周报数据
            
        Returns:
            转换后的数据，所有数值和日期字段都转换为字符串
        """
        def _deep_convert(obj: Any) -> Any:
            """递归把数值/date/datetime 转为字符串，适配嵌套结构的周报接口返回。"""
            if isinstance(obj, (int, float)):
                return str(obj)
            if hasattr(obj, "isoformat"):
                # date/datetime
                try:
                    return obj.isoformat()
                except Exception:
                    return str(obj)
            if isinstance(obj, dict):
                return {k: _deep_convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_deep_convert(v) for v in obj]
            return obj

        converted_data = _deep_convert(report_data)
        
        # 添加字段名映射，用于卡片展示
        from app.services.crm_config_service import add_field_mapping_to_data
        converted_data = add_field_mapping_to_data(converted_data, db_session, "周报")
        
        return converted_data
    
    def _convert_daily_report_data_for_feishu(self, db_session: Session, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将日报数据转换为飞书卡片所需的格式（所有数值和日期转换为字符串）
        
        Args:
            db_session: 数据库会话
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
        
        # 添加字段名映射，用于卡片展示
        from app.services.crm_config_service import add_field_mapping_to_data
        converted_data = add_field_mapping_to_data(converted_data, db_session, "日报")
        
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
                recorder_profile = user_profile_repo.get_by_recorder_id(db_session, recorder_id)
                if recorder_profile:
                    logger.info(f"Found profile by recorder_id: {recorder_id} -> {recorder_profile.name} (platform: {recorder_profile.oauth_user.provider})")
            
            # 2. 如果recorder_id没找到，再尝试通过姓名查找
            if not recorder_profile and recorder_name:
                recorder_profile = user_profile_repo.get_by_name(db_session, recorder_name)
                if recorder_profile:
                    logger.info(f"Found profile by name: {recorder_name} -> {recorder_profile.name} (platform: {recorder_profile.oauth_user.provider})")
            
            # 3. 获取用户的平台信息
            if recorder_profile and recorder_profile.oauth_user.provider:
                platform = recorder_profile.oauth_user.provider
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
                user_profile = user_profile_repo.get_by_oauth_user_id(db_session, ask_id)
                if not user_profile:
                    logger.warning(f"No user profile found for ask_id: {ask_id}, name: {name}")
                    continue
                
                # 获取用户的平台和open_id信息
                platform = user_profile.oauth_user.provider
                open_id = user_profile.oauth_user.open_id
                
                if not all([platform, open_id]):
                    logger.warning(f"User profile missing platform or open_id for ask_id: {ask_id}, name: {name}")
                    continue
                
                # 验证平台支持
                if platform not in [PLATFORM_FEISHU, PLATFORM_LARK, PLATFORM_DINGTALK]:
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

    def get_recipients_for_sales_task(
        self,
        db_session: Session,
        task_assignee_name: str = None,
        task_assignee_id: str = None
    ) -> List[Dict[str, Any]]:
        """获取销售任务卡片推送的接收者 - 目前只推送给任务负责人"""
        
        recipients = []
        
        # 1. 查找任务负责人的档案
        assignee_profile = None
        
        if task_assignee_id:
            # 优先使用task_assignee_id查找
            assignee_profile = user_profile_repo.get_by_recorder_id(db_session, task_assignee_id)
            if assignee_profile:
                logger.info(f"Found profile by task_assignee_id: {task_assignee_id} -> {assignee_profile.name}")
        
        if not assignee_profile and task_assignee_name:
            # 如果task_assignee_id没找到，使用姓名查找
            assignee_profile = user_profile_repo.get_by_name(db_session, task_assignee_name)
            if assignee_profile:
                logger.info(f"Found profile by name: {task_assignee_name} -> {assignee_profile.name}")
        
        if not assignee_profile:
            logger.warning(f"No profile found for sales task assignee: name={task_assignee_name}, id={task_assignee_id}")
            return recipients
        
        # 2. 只添加任务负责人本人
        assignee_open_id = assignee_profile.oauth_user.open_id
        if assignee_open_id:
            recipients.append({
                "open_id": assignee_open_id,
                "name": assignee_profile.name or task_assignee_name or "Unknown",
                "type": "task_assignee",
                "department": assignee_profile.department,
                "receive_id_type": "open_id",
                "platform": assignee_profile.oauth_user.provider
            })
            logger.info(f"Found sales task assignee: {assignee_profile.name} ({assignee_profile.department}) with {assignee_profile.oauth_user.provider} open_id: {assignee_open_id}")
        else:
            logger.warning(f"Task assignee {task_assignee_name} (profile: {assignee_profile.name}) has no {assignee_profile.oauth_user.provider} open_id, cannot send sales task notification")
        
        return recipients

    def send_sales_task_notification(
        self,
        db_session: Session,
        task_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """发送销售任务卡片通知"""
        
        task_assignee_id = task_data.get("assignee_id")
        task_assignee_name = task_data.get("assignee_name")
        if not task_assignee_name and not task_assignee_id:
            logger.warning("Sales task data missing assignee name and id")
            return {
                "success": False,
                "message": "Missing assignee name and id in sales task data",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 获取推送对象 - 销售任务只推送给任务负责人
        recipients = self.get_recipients_for_sales_task(
            db_session=db_session,
            task_assignee_name=task_assignee_name,
            task_assignee_id=task_assignee_id
        )
        
        if not recipients:
            logger.warning(f"No recipients found for sales task of {task_assignee_name}")
            return {
                "success": False,
                "message": f"No recipients found for {task_assignee_name}",
                "recipients_count": 0,
                "success_count": 0
            }
        
        # 构建模板变量
        template_vars = self._convert_sales_task_data_for_feishu(db_session, task_data)
        # 销售任务卡片模板
        template_id_by_platform = {
            PLATFORM_DINGTALK: "6e587206-a1fb-418f-9980-b67cd94d2e31.schema",
            PLATFORM_FEISHU: "AAqXTV4URg1ib",
            PLATFORM_LARK: "AAqXTV4URg1ib",
        }
        
        # 按平台分组接收者
        recipients_by_platform = self._group_recipients_by_platform(recipients)
        
        # 使用公共方法发送通知
        return self._send_notifications_by_platform(
            recipients_by_platform=recipients_by_platform,
            template_id_by_platform=template_id_by_platform,
            template_vars=template_vars,
            notification_type="sales task"
        )

    def _convert_sales_task_data_for_feishu(self, db_session: Session, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将销售任务数据转换为飞书卡片所需的格式（所有数值和日期转换为字符串）
        
        Args:
            db_session: 数据库会话
            task_data: 原始销售任务数据
            
        Returns:
            转换后的数据，所有数值和日期字段都转换为字符串
        """
        converted_data = task_data.copy()
        
        # 处理数值字段
        for key, value in converted_data.items():
            if isinstance(value, (int, float)):
                converted_data[key] = str(value)
            elif hasattr(value, 'isoformat'):  # 处理date对象
                converted_data[key] = value.isoformat()
        
        # 添加字段名映射，用于卡片展示
        from app.services.crm_config_service import add_field_mapping_to_data
        converted_data = add_field_mapping_to_data(converted_data, db_session, "销售任务")
        
        return converted_data


# 创建默认的平台通知服务实例
platform_notification_service = PlatformNotificationService()
