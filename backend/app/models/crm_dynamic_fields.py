"""
CRM动态字段模型定义
"""

from typing import Optional
from datetime import datetime
from sqlmodel import Field


class CRMDynamicFieldsMixin:
    """CRM动态字段混入类"""
    
    # 动态字段 - 用于推送卡片时的dynamic_fields参数
    record_type: Optional[str] = Field(default=None, description="记录类型")
    visit_purpose: Optional[str] = Field(default=None, description="拜访目的")
    visit_start_time: Optional[str] = Field(default=None, description="拜访开始时间")
    visit_end_time: Optional[str] = Field(default=None, description="拜访结束时间")
    counterpart_location: Optional[str] = Field(default=None, description="拜访地点")


class CRMDynamicFieldsAPIMixin:
    """CRM动态字段API模型混入类"""
    
    # 动态字段 - 用于推送卡片时的dynamic_fields参数
    record_type: Optional[str] = None  # 记录类型（字符串格式，支持枚举值转换）
    visit_purpose: Optional[str] = None  # 拜访目的
    visit_start_time: Optional[str] = None  # 拜访开始时间
    visit_end_time: Optional[str] = None  # 拜访结束时间
    counterpart_location: Optional[str] = None  # 拜访地点
