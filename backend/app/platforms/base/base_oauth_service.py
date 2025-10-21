import logging
from typing import Optional, Tuple
from urllib.parse import quote
from abc import ABC, abstractmethod
from app.utils.redis_client import redis_client

logger = logging.getLogger(__name__)


class BaseOAuthService(ABC):
    """OAuth服务基类"""
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
    
    # 抽象方法
    @property
    @abstractmethod
    def token_url(self) -> str:
        """获取访问令牌的URL"""
        pass
    
    @property
    @abstractmethod
    def auth_url(self) -> str:
        """OAuth授权URL"""
        pass
    
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称，用于Redis键前缀"""
        pass
    
    # 抽象方法 - 各平台必须实现自己的授权码换取token逻辑
    @abstractmethod
    def exchange_code_for_token(self, code: str, user_id: Optional[str] = None, type: str = None) -> Tuple[bool, str, Optional[str]]:
        """
        使用授权码换取访问令牌 - 各平台实现不同
        
        Args:
            code: 授权码
            user_id: 用户ID，如果提供则会将token存储到Redis
            type: token类型
            
        Returns:
            (success, message, access_token)
        """
        pass
    
    @abstractmethod
    def generate_auth_url(self, scope: str, redirect_uri: Optional[str] = None, state: Optional[str] = None) -> str:
        """
        生成授权URL - 各平台实现不同
        
        Args:
            scope: 授权范围
            redirect_uri: 授权成功后的回调地址
            state: 状态参数，用于防止CSRF攻击
            
        Returns:
            授权URL
        """
        pass
    
    # 通用方法（共享实现）
    def get_access_token_from_redis(self, user_id: str, type: str) -> Optional[str]:
        """从Redis获取访问令牌"""
        return redis_client._get_access_token(self.platform_name, user_id, type)
    
    def store_access_token_to_redis(self, user_id: str, access_token: str, type: str, expires_in: int = 7200) -> bool:
        """将访问令牌存储到Redis"""
        return redis_client._set_access_token(self.platform_name, user_id, access_token, type, expires_in)
