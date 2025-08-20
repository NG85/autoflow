from typing import Optional
from app.core.config import settings
from app.platforms.base.oauth_service import BaseOAuthService


class LarkOAuthService(BaseOAuthService):
    """Lark OAuth 授权服务（无状态）"""
    
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        super().__init__(
            app_id or settings.LARK_APP_ID,
            app_secret or settings.LARK_APP_SECRET
        )
    
    @property
    def token_url(self) -> str:
        return "https://open.larksuite.com/open-apis/authen/v1/access_token"
    
    @property
    def auth_url(self) -> str:
        return "https://open.larksuite.com/open-apis/authen/v1/index"
    
    @property
    def platform_name(self) -> str:
        return "lark"


# 创建默认的Lark OAuth服务实例
lark_oauth_service = LarkOAuthService()
