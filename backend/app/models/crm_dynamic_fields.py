"""
CRM动态字段模型定义
"""

from typing import Optional
from sqlmodel import Field


class CRMDynamicFieldsMixin:
    """CRM动态字段混入类"""
    
    # 动态字段 - 用于推送卡片时的dynamic_fields参数
    record_type: Optional[str] = Field(default=None, description="记录类型")
    visit_purpose: Optional[str] = Field(default=None, description="拜访目的")
    visit_start_time: Optional[str] = Field(default=None, description="拜访开始时间")
    visit_end_time: Optional[str] = Field(default=None, description="拜访结束时间")
    location: Optional[str] = Field(default=None, description="拜访地点")
    taken_at: Optional[str] = Field(default=None, description="拍摄时间")

