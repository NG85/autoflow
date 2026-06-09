import logging
import os
import re
import time
from typing import Dict, Any, Optional
from app.platforms.utils.url_parser import (
    UnsupportedDocumentTypeError,
    parse_platform_document_url,
    check_document_type_support,
    get_platform_from_url,
    parse_dingtalk_transcribe_url,
    parse_dingtalk_notable_url,
)
from app.platforms.feishu.oauth_service import feishu_oauth_service
from app.platforms.lark.oauth_service import lark_oauth_service
from app.platforms.dingtalk.client import DingTalkClient
from app.platforms.base.base_oauth_service import BaseOAuthService
from app.crm.file_processor import get_file_content_from_local_storage
from app.core.config import settings
from app.platforms.factory import get_client_by_url

logger = logging.getLogger(__name__)


class DocumentProcessingService:
    """通用文档处理服务，支持飞书、Lark、钉钉会议、钉钉听记和本地文件"""
    
    def __init__(self):
        self.feishu_oauth = feishu_oauth_service
        self.lark_oauth = lark_oauth_service
        self.dingtalk_client = DingTalkClient()
    
    def get_oauth_service_by_url(self, url: str) -> BaseOAuthService:
        """
        根据URL获取对应的OAuth服务
        
        Args:
            url: 飞书或Lark的URL
            
        Returns:
            对应的OAuth服务实例
        """
        if 'feishu.cn' in url:
            return self.feishu_oauth
        elif 'larksuite.com' in url:
            return self.lark_oauth
        else:
            logger.warning(f"无法识别URL类型，默认使用飞书OAuth服务: {url}")
            return self.feishu_oauth
    
    def process_document_url(
        self,
        document_url: str,
        user_id: str,
        auth_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理文档URL，支持飞书、Lark文档、钉钉会议、钉钉听记和本地文件
        
        Args:
            document_url: 文档URL或钉钉会议号
            user_id: 用户ID
            auth_code: 授权码（可选）
            
        Returns:
            处理结果字典
        """
        # 检查是否为钉钉会议号
        if self._is_dingtalk_room_code(document_url):
            return self._handle_dingtalk_conference(document_url)

        # 检查是否为钉钉听记链接
        if parse_dingtalk_transcribe_url(document_url):
            return self._handle_dingtalk_transcribe(document_url)
        
        # 检查是否为飞书/Lark链接
        if 'feishu.cn' in document_url or 'larksuite.com' in document_url:
            platform = get_platform_from_url(document_url)
            return self._handle_platform_document(document_url, user_id, auth_code, platform)
        else:
            return self._handle_local_document(document_url)
    
    def _is_dingtalk_room_code(self, document_url: str) -> bool:
        """
        检查是否为钉钉会议号，纯数字字符串，长度通常在6-12位之间
        
        Args:
            document_url: 待检查的字符串
            
        Returns:
            是否为钉钉会议号
        """
        if not document_url:
            return False
        
        # 去除首尾空格
        document_url = document_url.strip()
        
        # 检查是否为纯数字，且长度在合理范围内（6-12位）
        return bool(re.match(r'^\d{6,12}$', document_url))
    
    def _handle_platform_document(
        self,
        document_url: str,
        user_id: str,
        auth_code: Optional[str] = None,
        platform: str = "feishu"
    ) -> Dict[str, Any]:
        """处理平台文档（飞书/Lark）"""
        # 获取对应的OAuth服务
        oauth_service = self.get_oauth_service_by_url(document_url)
        
        # 检查文档类型是否支持
        try:
            url_type, doc_token = parse_platform_document_url(document_url)
            if not url_type or not doc_token:
                return {
                    "success": False,
                    "message": f"无法解析{oauth_service.platform_name}URL"
                }
            
            check_document_type_support(url_type, document_url)
        except UnsupportedDocumentTypeError as e:
            return {
                "success": False,
                "message": str(e),
                "unsupported_type": True
            }
        
        # 辅助函数：生成scope和授权URL
        def generate_auth_response(message: str, auth_expired: bool = False, auth_error: bool = False):
            scope_parts = []
            
            if url_type in ['doc', 'docx']:
                scope_parts.append("docx:document:readonly")
                scope_parts.append("docs:document.content:read")
            elif url_type == 'minutes':
                scope_parts.append("minutes:minutes:readonly")
                scope_parts.append("minutes:minutes.transcript:export")
            elif url_type == 'wiki_node':
                scope_parts.append("wiki:wiki:readonly")
                scope_parts.append("wiki:node:read")
            
            scope = " ".join(scope_parts)
            
            auth_url = oauth_service.generate_auth_url(scope=scope)
            
            data = {
                "auth_required": True,
                "auth_url": auth_url,
                "channel": platform,
                "url": document_url,
                "document_type": url_type
            }
            
            if auth_expired:
                data["auth_expired"] = True
            if auth_error:
                data["auth_error"] = True
            
            return {
                "success": False,
                "message": message,
                "data": data
            }
        
        # 首先尝试从Redis获取用户的access token
        logger.debug(f"app_id: {oauth_service.app_id}, app_secret: {oauth_service.app_secret}")
        access_token = oauth_service.get_access_token_from_redis(user_id, url_type)
        
        # 如果Redis中没有token，且提供了授权码，则换取token
        if not access_token and auth_code:
            try:
                success, message, token = oauth_service.exchange_code_for_token(
                    auth_code, user_id, url_type
                )
                
                if not success or not token:
                    # 授权码失败（可能过期），生成新的授权URL
                    logger.warning(f"Auth code exchange failed: {message}, generating new auth URL")
                    return generate_auth_response("授权码已过期，需要重新授权", auth_expired=True)
                
                access_token = token
                logger.info(f"Successfully exchanged auth code for token: {token}")
                
            except Exception as e:
                logger.error(f"Failed to exchange auth code for token: {e}")
                # 异常情况下也生成授权URL
                return generate_auth_response("授权处理异常，需要重新授权", auth_error=True)
        
        # 如果没有提供access token，生成授权URL
        if not access_token:
            return generate_auth_response(f"需要{oauth_service.platform_name}授权才能访问该链接")
        
        # 有access token，尝试获取内容
        try:
            # 使用新的平台工厂方法获取内容
            client = get_client_by_url(document_url)
            if client:
                original_content, document_type = client.get_content_from_source_with_token(
                    document_url, access_token
                )
                if not original_content:
                    return {
                        "success": False,
                        "message": f"未获取到{oauth_service.platform_name}内容，请检查链接或重新授权"
                    }
                return {
                    "success": True,
                    "content": original_content,
                    "document_type": document_type,
                    "title": None  # 平台文档通常没有文件名
                }
            else:
                return {
                    "success": False,
                    "message": f"无法识别URL类型: {document_url}"
                }
        except UnsupportedDocumentTypeError as e:
            return {
                "success": False,
                "message": str(e),
                "unsupported_type": True
            }
        except Exception as e:
            logger.error(f"Failed to get {oauth_service.platform_name} content: {e}")
            return {
                "success": False,
                "message": f"获取{oauth_service.platform_name}内容失败，请检查授权或重试"
            }
    
    def _handle_dingtalk_conference(
        self,
        room_code: str
    ) -> Dict[str, Any]:
        """
        处理钉钉会议号，获取会议录制的文本信息
        
        1. 首先根据会议号获取会议信息
        2. 然后根据会议信息中的会议ID和会议发起人unionId，获取会议录制的文本信息。
        
        Args:
            room_code: 钉钉会议号
            
        Returns:
            处理结果字典
        """
        
        # 获取钉钉应用的租户access_token
        access_token = self.dingtalk_client.get_tenant_access_token()
        
        # 1. 根据会议号获取会议ID
        conference_info = self.dingtalk_client.query_conference_info_by_room_code(
            room_code, access_token
        )
        
        if not conference_info:
            return {
                "success": False,
                "message": "无法获取会议信息，请检查会议号是否正确"
            }
        
        # 提取会议ID和创建者ID
        # API返回格式：{"conferenceList": [{"conferenceId": "...", "creatorId": "...", ...}], ...}
        conference_id = None
        creator_id = None
        if "conferenceList" in conference_info and isinstance(conference_info["conferenceList"], list) and len(conference_info["conferenceList"]) > 0:
            # 从conferenceList数组中取第一个会议的信息
            first_conference = conference_info["conferenceList"][0]
            conference_id = first_conference.get("conferenceId")
            creator_id = first_conference.get("creatorId")
        else:
            # 如果没有conferenceList，尝试直接获取字段
            conference_id = conference_info.get("conferenceId")
            creator_id = conference_info.get("creatorId")
        
        if not conference_id:
            logger.error(f"会议信息中缺少conferenceId: {conference_info}")
            return {
                "success": False,
                "message": "会议信息不完整，无法获取会议ID"
            }
        
        # 2. 获取会议发起人Id
        if not creator_id:
            return {
                "success": False,
                "message": "会议信息中缺少creatorId，无法获取会议发起人信息"
            }
        
        # 3. 查询会议录制的文本信息（支持分页读取）
        all_paragraphs = []
        next_token = None
        has_more = True
        max_iterations = 100  # 防止无限循环
        
        while has_more and len(all_paragraphs) < max_iterations:
            recording_text = self.dingtalk_client.query_cloud_recording_text(
                conference_id, creator_id, access_token, next_token=next_token
            )
            
            if not recording_text:
                if len(all_paragraphs) == 0:
                    # 第一次调用就失败
                    return {
                        "success": False,
                        "message": "无法获取会议录制文本，可能会议未开启录制或录制尚未完成"
                    }
                else:
                    # 后续调用失败，使用已获取的内容
                    logger.warning(f"分页读取会议录制文本时出错，已获取{len(all_paragraphs)}页内容")
                    break
            
            # 提取段落列表
            paragraph_list = recording_text.get("paragraphList", [])
            if paragraph_list:
                all_paragraphs.extend(paragraph_list)
            
            # 检查是否还有更多数据
            has_more = recording_text.get("hasMore", False)
            
            # 获取下一个分页令牌
            # 注意：返回字段是nextTtoken（拼写错误），但API参数是nextToken
            if paragraph_list:
                # 从最后一个段落获取nextTtoken
                last_paragraph = paragraph_list[-1]
                next_token = last_paragraph.get("nextTtoken")
                if not next_token:
                    # 如果没有nextTtoken，尝试从响应顶层获取
                    next_token = recording_text.get("nextToken")
            else:
                # 如果没有段落，尝试从响应顶层获取
                next_token = recording_text.get("nextToken")
            
            # 如果没有nextToken且hasMore为true，可能有问题，退出循环
            if has_more and not next_token:
                logger.warning("hasMore为true但没有nextToken，停止分页读取")
                break
        
        if not all_paragraphs:
            return {
                "success": False,
                "message": "会议录制文本为空"
            }
        
        # 合并所有段落的文本内容，保留说话人信息
        text_parts = []
        for paragraph in all_paragraphs:
            paragraph_text = paragraph.get("paragraph", "")
            if paragraph_text:
                nick_name = paragraph.get("nickName", "")
                if nick_name:
                    # 如果有说话人，格式为：[说话人] 文本内容
                    text_parts.append(f"[{nick_name}] {paragraph_text}")
                else:
                    # 如果没有说话人，直接使用文本内容
                    text_parts.append(paragraph_text)
        
        text_content = "\n".join(text_parts)
        
        if not text_content:
            return {
                "success": False,
                "message": "会议录制文本为空"
            }
        
        logger.info(f"成功获取会议录制文本，共{len(all_paragraphs)}个段落，总长度{len(text_content)}字符")
        
        return {
            "success": True,
            "content": text_content,
            "document_type": "dingtalk_conference",
            "title": f"钉钉会议_{room_code}_录制文本"
        }

    def _resolve_dingtalk_transcribe_config(self, transcribe_url: str) -> Dict[str, Any]:
        """校验听记链接与 AI 表格配置，返回上下文或错误。"""
        transcribe_id = parse_dingtalk_transcribe_url(transcribe_url)
        if not transcribe_id:
            return {"success": False, "message": "无法解析钉钉听记链接"}

        notable_url = settings.DINGTALK_TRANSCRIBE_NOTABLE_URL
        operator_id = settings.DINGTALK_NOTABLE_OPERATOR_UNION_ID
        if not notable_url:
            return {"success": False, "message": "听记功能未配置目标 AI 表格"}
        if not operator_id:
            return {"success": False, "message": "听记功能未配置 operator unionId"}

        base_id, sheet_id_or_name = parse_dingtalk_notable_url(notable_url)
        if not base_id or not sheet_id_or_name:
            return {
                "success": False,
                "message": "听记 AI 表格 URL 配置无效，需包含 baseId 和 sheetId 参数",
            }

        return {
            "success": True,
            "transcribe_id": transcribe_id,
            "transcribe_url": transcribe_url.strip(),
            "base_id": base_id,
            "sheet_id_or_name": sheet_id_or_name,
            "operator_id": operator_id,
            "link_field": settings.DINGTALK_TRANSCRIBE_LINK_FIELD,
            "content_field": settings.DINGTALK_TRANSCRIBE_CONTENT_FIELD,
        }

    def start_dingtalk_transcribe(self, transcribe_url: str) -> Dict[str, Any]:
        """
        同步写入听记链接到预建 AI 表格（供 HTTP 快速失败/重试）。

        AI 表格自动化回填的是听记总结（非逐字稿原文）。
        """
        config = self._resolve_dingtalk_transcribe_config(transcribe_url)
        if not config.get("success"):
            return config

        try:
            access_token = self.dingtalk_client.get_tenant_access_token()
        except Exception as e:
            logger.error("获取钉钉 access token 失败: %s", e)
            return {"success": False, "message": "获取钉钉访问令牌失败，请重试"}

        record_ids = self.dingtalk_client.create_notable_records(
            base_id=config["base_id"],
            sheet_id_or_name=config["sheet_id_or_name"],
            operator_id=config["operator_id"],
            records=[{"fields": {config["link_field"]: config["transcribe_url"]}}],
            access_token=access_token,
        )
        if not record_ids:
            return {"success": False, "message": "写入听记链接到 AI 表格失败，请重试"}

        notable_record_id = record_ids[0]
        logger.info(
            "已写入听记链接到 AI 表格: transcribe_id=%s, notable_record_id=%s",
            config["transcribe_id"],
            notable_record_id,
        )
        return {
            "success": True,
            "transcribe_id": config["transcribe_id"],
            "notable_record_id": notable_record_id,
        }

    def poll_dingtalk_transcribe_summary(
        self,
        notable_record_id: str,
        transcribe_id: str,
    ) -> Dict[str, Any]:
        """轮询 AI 表格，读取听记总结（Celery 异步执行）。"""
        notable_url = settings.DINGTALK_TRANSCRIBE_NOTABLE_URL
        operator_id = settings.DINGTALK_NOTABLE_OPERATOR_UNION_ID
        if not notable_url or not operator_id:
            return {"success": False, "message": "听记功能未配置"}

        base_id, sheet_id_or_name = parse_dingtalk_notable_url(notable_url)
        if not base_id or not sheet_id_or_name:
            return {"success": False, "message": "听记 AI 表格 URL 配置无效"}

        content_field = settings.DINGTALK_TRANSCRIBE_CONTENT_FIELD

        try:
            access_token = self.dingtalk_client.get_tenant_access_token()
        except Exception as e:
            logger.error("获取钉钉 access token 失败: %s", e)
            return {"success": False, "message": "获取钉钉访问令牌失败，请重试"}

        poll_interval = settings.DINGTALK_TRANSCRIBE_POLL_INTERVAL_SEC
        poll_timeout = settings.DINGTALK_TRANSCRIBE_POLL_TIMEOUT_SEC
        deadline = time.monotonic() + poll_timeout
        text_content = ""

        while time.monotonic() < deadline:
            record = self.dingtalk_client.get_notable_record(
                base_id=base_id,
                sheet_id_or_name=sheet_id_or_name,
                record_id=notable_record_id,
                operator_id=operator_id,
                access_token=access_token,
            )
            if record:
                fields = record.get("fields") or {}
                text_content = self._extract_notable_field_text(fields.get(content_field))
                if text_content:
                    break
            time.sleep(poll_interval)

        if not text_content:
            return {
                "success": False,
                "message": "听记总结获取超时，请稍后重试",
            }

        logger.info(
            "成功获取钉钉听记总结: transcribe_id=%s, length=%d",
            transcribe_id,
            len(text_content),
        )
        return {
            "success": True,
            "content": text_content,
            "document_type": "dingtalk_transcribe",
            "title": f"钉钉听记总结_{transcribe_id}",
        }

    def _handle_dingtalk_transcribe(self, transcribe_url: str) -> Dict[str, Any]:
        """处理钉钉听记链接：写表 + 轮询（同步全链路，非拜访 Celery 场景）。"""
        start_result = self.start_dingtalk_transcribe(transcribe_url)
        if not start_result.get("success"):
            return start_result
        return self.poll_dingtalk_transcribe_summary(
            notable_record_id=start_result["notable_record_id"],
            transcribe_id=start_result["transcribe_id"],
        )

    @staticmethod
    def _extract_notable_field_text(field_value: Any) -> str:
        """从 AI 表格字段值中提取文本内容。"""
        if field_value is None:
            return ""
        if isinstance(field_value, str):
            return field_value.strip()
        if isinstance(field_value, (int, float)):
            return str(field_value).strip()
        if isinstance(field_value, dict):
            for key in ("text", "link", "name", "value"):
                val = field_value.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        if isinstance(field_value, list) and field_value:
            parts = [
                DocumentProcessingService._extract_notable_field_text(item)
                for item in field_value
            ]
            return "\n".join(p for p in parts if p)
        return ""
    
    def _handle_local_document(self, document_url: str) -> Dict[str, Any]:
        """处理本地文档"""
        # 暂不支持除飞书外的其他网络链接，以及非本地挂载的存储路径
        if document_url.startswith("http") or not document_url.startswith(settings.STORAGE_PATH_PREFIX):
            return {
                "success": False,
                "message": "不支持的文件链接"
            }
        logger.info(f"Local file original url: {document_url}")
        # 处理文件路径 pingcap/data/customer-uploads/XXX.docx -> /shared/data/customer-uploads/XXX.docx
        full_document_url = document_url.replace(settings.STORAGE_TENANT, settings.LOCAL_FILE_STORAGE_PATH)
        logger.info(f"Local file parsed url: {full_document_url}")
        
        try:
            # 获取文件内容
            file_content, document_type = get_file_content_from_local_storage(full_document_url)
            
            if not file_content:
                return {
                    "success": False,
                    "message": "未获取到文件内容，请检查后重试"
                }
            
            return {
                "success": True,
                "content": file_content,
                "document_type": document_type,
                "title": os.path.basename(document_url)
            }
                
        except Exception as e:
            logger.error(f"Failed to read local document: {e}")
            return {
                "success": False,
                "message": "读取本地文件失败，请重试"
            }

document_processing_service = DocumentProcessingService()