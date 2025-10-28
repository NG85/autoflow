from typing import List, Optional
from pydantic import BaseModel, Field

class VisitRecordCreateRequest(BaseModel):
    """OLM拜访记录创建请求"""
    account: int = Field(..., description="拜访客户ID (必填)")
    dim_depart: int = Field(..., alias="dimDepart", description="所属部门ID")
    custom_item3: str = Field(..., alias="customItem3__c", description="拜访方式: 客户现场拜访, 线下拜访客户, 线上会议, 来我司拜访, 饭局聚会, 电话/录音, 其他")
    sign_in_date: int = Field(..., alias="signInDate", description="签到时间 (毫秒时间戳)")
    sign_out_date: int = Field(..., alias="signOutDate", description="签退时间 (毫秒时间戳)")
    custom_item5: str = Field(..., alias="customItem5__c", description="本次拜访目的 (最多300个字符)")
    custom_item2: str = Field(..., alias="customItem2__c", description="拜访事项及结果记录 (最多300个字符)")
    custom_item6: int = Field(..., alias="customItem6__c", description="是否新客户: 1=是, 2=否")
    owner_id: int = Field(..., alias="ownerId", description="所有人ID")
    source_record_id: str = Field(..., description="来源拜访记录ID (用于日志追溯)")

    class Config:
        allow_population_by_field_name = True

class VisitRecordBatchCreateRequest(BaseModel):
    visit_records: List[VisitRecordCreateRequest]
    partial_fail: bool = True
