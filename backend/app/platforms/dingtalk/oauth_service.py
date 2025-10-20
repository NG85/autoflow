from typing import Optional, Tuple
import logging
import requests
from urllib.parse import quote
from app.core.config import settings
from app.platforms.base.base_oauth_service import BaseOAuthService

logger = logging.getLogger(__name__)


class DingTalkOAuthService(BaseOAuthService):
    """钉钉 OAuth 授权服务"""
    
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        super().__init__(
            app_id or settings.DINGTALK_APP_ID,
            app_secret or settings.DINGTALK_APP_SECRET
        )
    
    @property
    def base_url(self) -> str:
        return "https://api.dingtalk.com"
    
    @property
    def token_url(self) -> str:
        # 钉钉使用不同的token URL
        return "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
    
    @property
    def auth_url(self) -> str:
        return "https://login.dingtalk.com/oauth2/auth"
    
    @property
    def platform_name(self) -> str:
        return "dingtalk"
    
    def generate_auth_url(self, scope: str, redirect_uri: Optional[str] = None, state: Optional[str] = None) -> str:
        """
        生成授权URL - 钉钉实现
        
        Args:
            scope: 授权范围（钉钉使用不同的scope格式）
            redirect_uri: 授权成功后的回调地址
            state: 状态参数，用于防止CSRF攻击
            
        Returns:
            授权URL
        """
        # 钉钉使用不同的参数名
        params = {
            'client_id': self.app_id,  # 钉钉使用client_id而不是app_id
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': scope,
            # 'state': state,
            'prompt': 'consent'
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
        使用授权码换取访问令牌 - 钉钉实现
        
        Args:
            code: 授权码
            user_id: 用户ID，如果提供则会将token存储到Redis
            type: 令牌类型
            
        Returns:
            (success, message, access_token)
        """
        try:
            # 钉钉的token获取使用不同的参数格式
            data = {
                'clientId': self.app_id,
                'clientSecret': self.app_secret,
                'code': code,
                'grantType': 'authorization_code'
            }
            
            response = requests.post(self.token_url, json=data)
            response.raise_for_status()
            
            result = response.json()
            # 钉钉可能直接返回access_token，或者在特定字段中
            access_token = result.get('accessToken')
            expires_in = result.get('expireIn', 7200)  # 钉钉使用expireIn
            
            if access_token:
                # 如果提供了用户ID，将token存储到Redis
                if user_id and type:
                    self.store_access_token_to_redis(user_id, access_token, type, expires_in)
                
                logger.info("Successfully exchanged code for dingtalk access token")
                return True, "钉钉授权成功", access_token
            else:
                logger.error(f"No access token in dingtalk response: {result}")
                return False, "获取钉钉访问令牌失败", None
                
        except Exception as e:
            logger.error(f"Error exchanging code for dingtalk token: {e}")
            return False, f"钉钉网络错误: {str(e)}", None


# 创建默认的钉钉OAuth服务实例
dingtalk_oauth_service = DingTalkOAuthService()
