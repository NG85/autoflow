from typing import List, Optional
from pydantic import BaseModel, Field

class OlmVisitRecordCreateRequest(BaseModel):
    """OLM拜访记录创建请求"""
    account: Optional[int] = Field(None, description="拜访客户ID (必填)")
    dim_depart: Optional[int] = Field(None, description="所属部门ID")
    custom_item3: Optional[str] = Field(None, description="拜访方式: 客户现场拜访, 线下拜访客户, 线上会议, 来我司拜访, 饭局聚会, 电话/录音, 其他")
    sign_in_date: Optional[int] = Field(None, description="签到时间 (毫秒时间戳)")
    sign_out_date: Optional[int] = Field(None, description="签退时间 (毫秒时间戳)")
    custom_item5: Optional[str] = Field(None, description="本次拜访目的 (最多300个字符)")
    custom_item2: Optional[str] = Field(None, description="拜访事项及结果记录 (最多300个字符)")
    custom_item6: Optional[int] = Field(None, description="是否新客户: 1=是, 2=否")
    owner_id: Optional[int] = Field(None, description="所有人ID")
    source_record_id: Optional[str] = Field(None, description="来源拜访记录ID (用于日志追溯)")
    created_by: Optional[int] = Field(None, description="创建人ID")
    created_at: Optional[int] = Field(None, description="创建日期 (毫秒时间戳)")

class OlmVisitRecordBatchCreateRequest(BaseModel):
    visit_records: List[OlmVisitRecordCreateRequest]
    partial_fail: bool = True

class ChaitinVisitRecordCreateRequest(BaseModel):
    """长亭拜访记录创建请求"""
    company_id: Optional[int] = Field(None, description="拜访客户ID")
    content: Optional[str] = Field(None, description="拜访内容")
    username: Optional[str] = Field(None, description="长亭CRM用户名")

class ChaitinVisitRecordBatchCreateRequest(BaseModel):
    followup_records: List[ChaitinVisitRecordCreateRequest]
    partial_fail: bool = True