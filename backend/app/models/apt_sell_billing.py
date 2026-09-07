from typing import Optional

from sqlmodel import Field, SQLModel


class AptSellBilling(SQLModel, table=True):
    """
    伙伴 AI / aptSell 计费 SKU 目录。表由计费服务维护，本系统只读。

    ``status``：0 或 NULL 为在用，非 0 为停用；缺行或读表失败视为默认开通。
    """

    __tablename__ = "apt_sell_billing"

    id: Optional[int] = Field(default=None, primary_key=True)
    ai_module_key: Optional[str] = Field(default=None, max_length=200)
    ai_module: str = Field(max_length=200)
    status: Optional[int] = Field(default=0, description="SKU 状态：0或NULL=在用，非0=停用")
