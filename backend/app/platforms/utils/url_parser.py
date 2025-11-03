"""
URL解析工具
"""

import re
from typing import Tuple, Optional
from urllib.parse import parse_qs, urlparse

import requests


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
    解析飞书多维表格/知识空间URL，返回(url_type, token, table_id, view_id)
    url_type: 'base' or 'wiki'
    """
    if not url:
        return None, None, None, None
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    url_type = None
    token = None
    for i, part in enumerate(path_parts):
        if part in ('base', 'wiki') and i + 1 < len(path_parts):
            url_type = part
            token = path_parts[i + 1]
            break
    qs = parse_qs(parsed.query)
    table_id = qs.get('table', [None])[0]
    view_id = qs.get('view', [None])[0]
    return url_type, token, table_id, view_id


def resolve_bitable_app_token(token, url_type, url_token) -> str:
    """
    根据url类型自动获取app_token：
    - base/xxx 直接用xxx
    - wiki/xxx 需GET https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?obj_type=wiki&token=xxx 拿obj_token
    """
    if not url_token:
        return None
    if url_type == 'wiki':
        node_token = url_token
        url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?obj_type=wiki&token={node_token}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(resp.text)
            return None
        resp.raise_for_status()
        data = resp.json().get("data", {})
        obj_token = data.get("node", {}).get("obj_token")
        if not obj_token:
            raise ValueError(f"未能通过wiki node获取到obj_token: {resp.text}")
        return obj_token
    # base/直接用
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
