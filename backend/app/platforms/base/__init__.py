"""
平台基础抽象类包

包含所有平台的基础抽象类和实现类。
"""

from .client import BaseLarkClient
from .oauth_service import BaseOAuthService

__all__ = [
    'BaseLarkClient',
    'BaseOAuthService', 
    'BaseLarkClientImpl',
    'BaseOAuthServiceImpl'
]
