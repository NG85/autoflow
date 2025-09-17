"""
CRM动态字段模型定义
"""

from typing import Optional
from datetime import datetime
from sqlmodel import Field


class CRMDynamicFieldsMixin:
    """CRM动态字段混入类"""
    
    # 动态字段 - 用于推送卡片时的dynamic_fields参数
    visit_start_time: Optional[datetime] = Field(default=None, description="拜访开始时间")
    visit_end_time: Optional[datetime] = Field(default=None, description="拜访结束时间")


class CRMDynamicFieldsAPIMixin:
    """CRM动态字段API模型混入类"""
    
    # 动态字段 - 用于推送卡片时的dynamic_fields参数
    visit_start_time: Optional[datetime] = None  # 拜访开始时间
    visit_end_time: Optional[datetime] = None  # 拜访结束时间
