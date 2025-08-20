from typing import Optional
from app.core.config import settings
from app.platforms.base.client import BaseLarkClient


class FeishuClient(BaseLarkClient):
    """飞书客户端实现"""
    
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        super().__init__(
            app_id or settings.FEISHU_APP_ID,
            app_secret or settings.FEISHU_APP_SECRET
        )
    
    @property
    def base_url(self) -> str:
        return "https://open.feishu.cn/open-apis"
    
    @property
    def auth_url(self) -> str:
        return "https://open.feishu.cn/open-apis/authen/v1/index"
    
    @property
    def token_url(self) -> str:
        return f"{self.base_url}/auth/v3/tenant_access_token/internal/"
    
    @property
    def platform_name(self) -> str:
        return "feishu"


# 创建默认的飞书客户端实例
feishu_client = FeishuClient()
