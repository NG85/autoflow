"""
URL解析工具
"""

import re
from typing import Tuple, Optional
from urllib.parse import urlparse


class UnsupportedDocumentTypeError(Exception):
    """不支持的文档类型异常"""
    pass


def parse_platform_document_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    解析平台URL，返回文档类型和令牌
    
    Args:
        url: 平台URL
        
    Returns:
        (文档类型, 文档令牌) 的元组
    """
    if not url:
        return None, None
    
    # 文档URL模式
    doc_patterns = [
        r'https?://[^/]+/docs/([a-zA-Z0-9]+)',
        r'https?://[^/]+/docx/([a-zA-Z0-9]+)',
    ]
    
    # 会议纪要URL模式
    minutes_patterns = [
        r'https?://[^/]+/minutes/([a-zA-Z0-9]+)',
    ]
    
    # 知识库URL模式
    wiki_patterns = [
        r'https?://[^/]+/wiki/([a-zA-Z0-9]+)',
    ]
    
    # 检查文档类型
    for pattern in doc_patterns:
        match = re.search(pattern, url)
        if match:
            return 'doc', match.group(1)
    
    for pattern in minutes_patterns:
        match = re.search(pattern, url)
        if match:
            return 'minutes', match.group(1)
    
    for pattern in wiki_patterns:
        match = re.search(pattern, url)
        if match:
            return 'wiki_node', match.group(1)
    
    return None, None


def check_document_type_support(url_type: str, url: str) -> None:
    """
    检查文档类型是否支持
    
    Args:
        url_type: 文档类型
        url: 文档URL
        
    Raises:
        UnsupportedDocumentTypeError: 不支持的文档类型
    """
    from app.platforms.constants import SUPPORTED_DOCUMENT_TYPES
    
    if url_type not in SUPPORTED_DOCUMENT_TYPES:
        raise UnsupportedDocumentTypeError(
            f"不支持的文档类型: {url_type}，URL: {url}。"
            f"支持的文档类型: {', '.join(SUPPORTED_DOCUMENT_TYPES)}"
        )


def parse_bitable_url(url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    解析Bitable URL
    
    Args:
        url: Bitable URL
        
    Returns:
        (URL类型, URL令牌, 表格ID, 视图ID) 的元组
    """
    if not url:
        return None, None, None, None
    
    # Bitable URL模式
    bitable_pattern = r'https?://[^/]+/base/([a-zA-Z0-9]+)/([a-zA-Z0-9]+)/([a-zA-Z0-9]+)'
    match = re.search(bitable_pattern, url)
    
    if match:
        url_token = match.group(1)
        table_id = match.group(2)
        view_id = match.group(3)
        return 'bitable', url_token, table_id, view_id
    
    return None, None, None, None


def resolve_bitable_app_token(url_token: str) -> str:
    """
    解析Bitable应用令牌
    
    Args:
        url_token: URL令牌
        
    Returns:
        应用令牌
    """
    # 这里可能需要根据实际情况实现
    # 暂时返回URL令牌作为应用令牌
    return url_token


def is_feishu_url(url: str) -> bool:
    """
    检查是否为飞书URL
    
    Args:
        url: 要检查的URL
        
    Returns:
        是否为飞书URL
    """
    if not url:
        return False
    
    parsed_url = urlparse(url)
    return 'feishu.cn' in parsed_url.netloc


def is_lark_url(url: str) -> bool:
    """
    检查是否为Lark URL
    
    Args:
        url: 要检查的URL
        
    Returns:
        是否为Lark URL
    """
    if not url:
        return False
    
    parsed_url = urlparse(url)
    return 'larksuite.com' in parsed_url.netloc


def get_platform_from_url(url: str) -> Optional[str]:
    """
    从URL获取平台名称
    
    Args:
        url: 要检查的URL
        
    Returns:
        平台名称 (feishu/lark) 或 None
    """
    if is_lark_url(url):
        return 'lark'
    elif is_feishu_url(url):
        return 'feishu'
    else:
        return None
