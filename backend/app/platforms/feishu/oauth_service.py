from typing import Optional
from app.core.config import settings
from app.platforms.base.lark_oauth_service import BaseLarkOAuthService


class FeishuOAuthService(BaseLarkOAuthService):
    """飞书 OAuth 授权服务（无状态）"""
    
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        super().__init__(
            app_id or settings.FEISHU_APP_ID,
            app_secret or settings.FEISHU_APP_SECRET
        )
    
    @property
    def token_url(self) -> str:
        return "https://open.feishu.cn/open-apis/authen/v1/access_token"
    
    @property
    def auth_url(self) -> str:
        return "https://open.feishu.cn/open-apis/authen/v1/index"
    
    @property
    def platform_name(self) -> str:
        return "feishu"


# 创建默认的飞书OAuth服务实例
feishu_oauth_service = FeishuOAuthService()
