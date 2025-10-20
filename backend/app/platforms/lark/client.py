from typing import Optional
from app.core.config import settings
from app.platforms.base.lark_client import BaseLarkClient


class LarkClient(BaseLarkClient):
    """Lark客户端实现"""
    
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        super().__init__(
            app_id or settings.LARK_APP_ID,
            app_secret or settings.LARK_APP_SECRET
        )
    
    @property
    def base_url(self) -> str:
        return "https://open.larksuite.com/open-apis"
     
    @property
    def platform_name(self) -> str:
        return "lark"


# 创建默认的Lark客户端实例
lark_client = LarkClient()
