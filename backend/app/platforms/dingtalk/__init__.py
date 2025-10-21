"""
钉钉平台实现包

包含钉钉客户端和OAuth服务的实现。
"""

from .client import DingTalkClient, dingtalk_client
from .oauth_service import DingTalkOAuthService, dingtalk_oauth_service

__all__ = [
    'DingTalkClient',
    'dingtalk_client',
    'DingTalkOAuthService', 
    'dingtalk_oauth_service'
]
