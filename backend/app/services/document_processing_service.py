import logging
import os
from typing import Dict, Any, Optional, Tuple
from uuid import UUID
from sqlmodel import Session
from app.feishu.common_open import (
    parse_feishu_url,
    check_document_type_support,
    UnsupportedDocumentTypeError,
    get_content_from_feishu_source_with_token
)
from app.feishu.oauth_service import FeishuOAuthService
from app.crm.file_processor import get_file_content_from_local_storage
from app.core.config import settings

logger = logging.getLogger(__name__)


class DocumentProcessingService:
    """通用文档处理服务"""
    
    def __init__(self):
        self.oauth_service = FeishuOAuthService()
    
    def process_document_url(
        self,
        document_url: str,
        user_id: str,
        feishu_auth_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理文档URL，支持飞书文档和本地文件
        
        Args:
            document_url: 文档URL
            user_id: 用户ID
            feishu_auth_code: 飞书授权码（可选）
            
        Returns:
            处理结果字典
        """
        # 检查是否为飞书链接
        if 'feishu.cn' in document_url or 'larksuite.com' in document_url:
            return self._handle_feishu_document(document_url, user_id, feishu_auth_code)
        else:
            return self._handle_local_document(document_url)
    
    def _handle_feishu_document(
        self,
        document_url: str,
        user_id: str,
        feishu_auth_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """处理飞书文档"""
        # 检查文档类型是否支持
        try:
            url_type, doc_token = parse_feishu_url(document_url)
            if not url_type or not doc_token:
                return {
                    "success": False,
                    "message": "无法解析飞书URL"
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
            
            auth_url = self.oauth_service.generate_auth_url(scope=scope)
            
            data = {
                "auth_required": True,
                "auth_url": auth_url,
                "channel": "feishu",
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
        
        # 首先尝试从Redis获取用户的飞书access token
        logger.debug(f"app_id: {self.oauth_service.app_id}, app_secret: {self.oauth_service.app_secret}")
        feishu_access_token = self.oauth_service.get_access_token_from_redis(user_id, url_type)
        
        # 如果Redis中没有token，且提供了授权码，则换取token
        if not feishu_access_token and feishu_auth_code:
            try:
                success, message, access_token = self.oauth_service.exchange_code_for_token(
                    feishu_auth_code, user_id, url_type
                )
                
                if not success or not access_token:
                    # 授权码失败（可能过期），生成新的授权URL
                    logger.warning(f"Auth code exchange failed: {message}, generating new auth URL")
                    return generate_auth_response("授权码已过期，需要重新授权", auth_expired=True)
                
                feishu_access_token = access_token
                logger.info(f"Successfully exchanged auth code for token: {access_token}")
                
            except Exception as e:
                logger.error(f"Failed to exchange auth code for token: {e}")
                # 异常情况下也生成授权URL
                return generate_auth_response("授权处理异常，需要重新授权", auth_error=True)
        
        # 如果没有提供飞书access token，生成授权URL
        if not feishu_access_token:
            return generate_auth_response("需要飞书授权才能访问该链接")
        
        # 有access token，尝试获取内容
        try:
            original_content, document_type = get_content_from_feishu_source_with_token(
                document_url, feishu_access_token
            )
            
            if not original_content:
                return {
                    "success": False,
                    "message": "未获取到飞书内容，请检查链接或重新授权"
                }
            
            return {
                "success": True,
                "content": original_content,
                "document_type": document_type,
                "title": None  # 飞书文档通常没有文件名
            }
                
        except UnsupportedDocumentTypeError as e:
            return {
                "success": False,
                "message": str(e),
                "unsupported_type": True
            }
        except Exception as e:
            logger.error(f"Failed to get feishu content: {e}")
            return {
                "success": False,
                "message": "获取飞书内容失败，请检查授权或重试"
            }
    
    def _handle_local_document(self, document_url: str) -> Dict[str, Any]:
        """处理本地文档"""
        # 暂不支持除飞书外的其他网络链接，以及非本地挂载的存储路径
        if document_url.startswith("http") or not document_url.startswith(settings.STORAGE_PATH_PREFIX):
            return {
                "success": False,
                "message": "不支持的文件链接"
            }
        
        # 处理文件路径 data/customer-uploads/XXX.docx -> /shared/data/customer-uploads/XXX.docx
        full_document_url = document_url.replace('data', settings.LOCAL_FILE_STORAGE_PATH)
        logger.info(f"Local file detected: {full_document_url}")
        
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
