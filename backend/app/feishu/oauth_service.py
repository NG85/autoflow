import logging
from typing import Optional, Tuple
import requests
from app.core.config import settings
from app.utils.redis_client import redis_client

logger = logging.getLogger(__name__)


class FeishuOAuthService:
    """飞书 OAuth 授权服务（无状态）"""
    
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        self.app_id = app_id or settings.FEISHU_APP_ID
        self.app_secret = app_secret or settings.FEISHU_APP_SECRET
        self.token_url = "https://open.feishu.cn/open-apis/authen/v1/access_token"
    
    def get_access_token_from_redis(self, user_id: str, type: str) -> Optional[str]:
        return redis_client.get_feishu_access_token(user_id, type)
    
    def store_access_token_to_redis(self, user_id: str, access_token: str, type: str, expires_in: int = 7200) -> bool:
        return redis_client.set_feishu_access_token(user_id, access_token, type, expires_in)
    
    def generate_auth_url(self, scope: str, redirect_uri: Optional[str] = None, state: Optional[str] = None) -> str:
        """
        生成飞书授权URL
        
        Args:
            scope: 授权范围，多个scope用空格分隔
            redirect_uri: 授权成功后的回调地址
            state: 状态参数，用于防止CSRF攻击
            
        Returns:
            授权URL
        """
        base_url = "https://open.feishu.cn/open-apis/authen/v1/index"
        params = {
            'app_id': self.app_id,
            'redirect_uri': redirect_uri,
            # 'state': state,
            'scope': scope
        }        
            
        # 构建查询字符串，对参数值进行URL编码
        from urllib.parse import quote
        query_parts = []
        for k, v in params.items():
            if v:
                query_parts.append(f"{k}={quote(str(v))}")
        query_string = '&'.join(query_parts)
        return f"{base_url}?{query_string}"
     
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
