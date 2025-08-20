import logging
from typing import Optional, Tuple
from app.platforms.feishu.client import feishu_client
from app.platforms.lark.client import lark_client
from app.platforms.base.client import BaseLarkClient

logger = logging.getLogger(__name__)


def get_client_by_url(url: str) -> BaseLarkClient:
    """
    根据URL获取对应的客户端实例
    
    Args:
        url: 飞书或Lark的URL
        
    Returns:
        对应的客户端实例
    """
    if 'feishu.cn' in url:
        return feishu_client
    elif 'larksuite.com' in url:
        return lark_client
    else:
        logger.warning(f"无法识别URL类型: {url}")
        return None


def get_content_from_source(url: str, access_token: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从URL获取内容
    
    Args:
        url: 文档URL
        access_token: 访问令牌
        
    Returns:
        (content, content_type)
    """
    client = get_client_by_url(url)
    if client:
        return client.get_content_from_source_with_token(url, access_token)
    else:
        return url, None


def send_message_by_url(url: str, receive_id: str, token: str, text: str,
                        receive_id_type: str = "open_id", msg_type: str = "text"):
    """
    根据URL发送消息
    
    Args:
        url: 平台URL（用于判断平台）
        receive_id: 接收者ID
        token: 访问令牌
        text: 消息内容
        receive_id_type: 接收者ID类型
        msg_type: 消息类型
    """
    client = get_client_by_url(url)
    if client:
        return client.send_message(receive_id, token, text, receive_id_type, msg_type)
    else:
        return None
