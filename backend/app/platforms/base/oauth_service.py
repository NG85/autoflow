import logging
from typing import Optional, Tuple
import requests
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
    
    # 具体方法（共享实现）
    def get_access_token_from_redis(self, user_id: str, type: str) -> Optional[str]:
        """从Redis获取访问令牌"""
        return redis_client._get_access_token(self.platform_name, user_id, type)
    
    def store_access_token_to_redis(self, user_id: str, access_token: str, type: str, expires_in: int = 7200) -> bool:
        """将访问令牌存储到Redis"""
        return redis_client._set_access_token(self.platform_name, user_id, access_token, type, expires_in)
    
    def generate_auth_url(self, scope: str, redirect_uri: Optional[str] = None, state: Optional[str] = None) -> str:
        """
        生成授权URL
        
        Args:
            scope: 授权范围，多个scope用空格分隔
            redirect_uri: 授权成功后的回调地址
            state: 状态参数，用于防止CSRF攻击
            
        Returns:
            授权URL
        """
        params = {
            'app_id': self.app_id,
            'redirect_uri': redirect_uri,
            # 'state': state,
            'scope': scope
        }        
            
        # 构建查询字符串，对参数值进行URL编码
        query_parts = []
        for k, v in params.items():
            if v:
                query_parts.append(f"{k}={quote(str(v))}")
        query_string = '&'.join(query_parts)
        return f"{self.auth_url}?{query_string}"
     
    def exchange_code_for_token(self, code: str, user_id: Optional[str] = None, type: str = None) -> Tuple[bool, str, Optional[str]]:
        """
        使用授权码换取访问令牌
        
        Args:
            code: 授权码
            user_id: 用户ID，如果提供则会将token存储到Redis
            
        Returns:
            (success, message, access_token)
        """
        try:
            data = {
                'grant_type': 'authorization_code',
                'app_id': self.app_id,
                'app_secret': self.app_secret,
                'code': code
            }
            
            response = requests.post(self.token_url, json=data)
            response.raise_for_status()
            
            result = response.json()
            if result.get('code') == 0:
                token_data = result.get('data', {})
                access_token = token_data.get('access_token')
                expires_in = token_data.get('expires_in', 7200)  # 默认2小时
                
                if access_token:
                    # 如果提供了用户ID，将token存储到Redis
                    if user_id and type:
                        self.store_access_token_to_redis(user_id, access_token, type, expires_in)
                    
                    logger.info("Successfully exchanged code for access token")
                    return True, "授权成功", access_token
                else:
                    logger.error("No access token in response")
                    return False, "获取访问令牌失败", None
            else:
                logger.error(f"Failed to exchange code for token: {result}")
                return False, f"授权失败: {result.get('msg', 'Unknown error')}", None
                
        except Exception as e:
            logger.error(f"Error exchanging code for token: {e}")
            return False, f"网络错误: {str(e)}", None
