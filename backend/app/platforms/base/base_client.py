from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)


# 自定义异常类
class UnsupportedDocumentTypeError(Exception):
    """不支持的文档类型异常"""
    pass


def log_content_preview(content: str, max_length: int = 100, prefix: str = "内容") -> str:
    """
    生成内容预览用于日志记录，避免日志过长
    
    Args:
        content: 原始内容
        max_length: 最大显示长度
        prefix: 日志前缀
        
    Returns:
        格式化的日志字符串
    """
    if not content:
        return f"{prefix}: (空)"
    
    content_length = len(content)
    if content_length <= max_length:
        return f"{prefix}: {content}"
    else:
        preview = content[:max_length]
        return f"{prefix}: {preview}... (总长度: {content_length})"


class BaseClient(ABC):
    """平台客户端基类 - 所有平台的通用接口"""
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    # 抽象方法 - 所有平台必须实现
    @property
    @abstractmethod
    def base_url(self) -> str:
        """API基础URL"""
        pass
    
    @property
    @abstractmethod
    def auth_url(self) -> str:
        """OAuth授权URL"""
        pass
    
    @property
    @abstractmethod
    def token_url(self) -> str:
        """获取访问令牌的URL"""
        pass
        
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称，用于日志和文档类型"""
        pass
    
    # 抽象方法 - 平台特定的实现
    @abstractmethod
    def parse_url(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """解析URL，返回(url_type, token)"""
        pass
    
    @abstractmethod
    def get_content_from_source_with_token(self, url: str, access_token: str) -> Tuple[Optional[str], Optional[str]]:
        """从源获取内容"""
        pass
    
    @abstractmethod
    def send_message(self, receive_id: str, token: str, text: str, receive_id_type: str = "open_id", msg_type: str = "text") -> Dict[str, Any]:
        """发送消息"""
        pass
    
    # 抽象方法 - 各平台必须实现自己的获取token方式
    @abstractmethod
    def get_tenant_access_token(self, app_id: Optional[str] = None, app_secret: Optional[str] = None) -> str:
        """获取访问令牌 - 各平台实现不同"""
        pass
    
    def check_document_type_support(self, url_type: str, url: str) -> None:
        """
        检查文档类型是否支持 - 默认实现，子类可重写
        
        Args:
            url_type: 解析出的文档类型
            url: 原始URL
            
        Raises:
            UnsupportedDocumentTypeError: 当文档类型不支持时抛出
        """
        # 默认实现，子类可以重写以定义自己的支持类型
        if url_type not in self.get_supported_document_types():
            raise UnsupportedDocumentTypeError(f"不支持的{self.platform_name}内容类型: {url_type}")
    
    def get_supported_document_types(self) -> set:
        """获取支持的文档类型 - 子类可重写"""
        return set()
    
    def get_unsupported_document_types(self) -> set:
        """获取暂不支持但可识别的文档类型 - 子类可重写"""
        return set()
