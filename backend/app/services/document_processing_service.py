import logging
import os
from typing import Dict, Any, Optional
from app.platforms.utils.url_parser import (
    UnsupportedDocumentTypeError,
    parse_platform_document_url,
    check_document_type_support,
    get_platform_from_url
)
from app.platforms.feishu.oauth_service import feishu_oauth_service
from app.platforms.lark.oauth_service import lark_oauth_service
from app.platforms.base.oauth_service import BaseOAuthService
from app.crm.file_processor import get_file_content_from_local_storage
from app.core.config import settings
from app.platforms.factory import get_client_by_url

logger = logging.getLogger(__name__)


class DocumentProcessingService:
    """通用文档处理服务，支持飞书、Lark和本地文件"""
    
    def __init__(self):
        self.feishu_oauth = feishu_oauth_service
        self.lark_oauth = lark_oauth_service
    
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
        处理文档URL，支持飞书、Lark文档和本地文件
        
        Args:
            document_url: 文档URL
            user_id: 用户ID
            auth_code: 授权码（可选）
            
        Returns:
            处理结果字典
        """
        # 检查是否为飞书/Lark链接
        if 'feishu.cn' in document_url or 'larksuite.com' in document_url:
            platform = get_platform_from_url(document_url)
            return self._handle_platform_document(document_url, user_id, auth_code, platform)
        else:
            return self._handle_local_document(document_url)
    
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