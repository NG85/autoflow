import enum
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
    sign_in_address: Optional[str] = Field(None, description="签到地址")
    custom_item7: Optional[int] = Field(None, description="拜访拍照时间（毫秒时间戳）")

class OlmVisitRecordBatchCreateRequest(BaseModel):
    visit_records: List[OlmVisitRecordCreateRequest]
    partial_fail: bool = True

class ChaitinVisitRecordCreateRequest(BaseModel):
    """长亭拜访记录创建请求"""
    company_id: Optional[str] = Field(None, description="拜访客户ID")
    content: Optional[str] = Field(None, description="拜访内容")
    username: Optional[str] = Field(None, description="长亭CRM用户名")
    project_id: Optional[str] = Field(None, description="商机ID(可选)")
    source_record_id: Optional[str] = Field(None, description="来源拜访记录ID")

class ChaitinVisitRecordBatchCreateRequest(BaseModel):
    followup_records: List[ChaitinVisitRecordCreateRequest]
    partial_fail: bool = True


class WebeyeVisitRecordCreateRequest(BaseModel):
    """网眼（简道云 activities）拜访/跟进记录 upsert 请求。

    线索拜访与客户拜访关联字段互斥：线索传 ``lead_id`` 勿传 ``account_id``；
    客户传 ``account_id`` 勿传 ``lead_id``。
    """

    source_record_id: Optional[str] = Field(None, description="来源侧记录 ID（幂等键）")
    data_id: Optional[str] = Field(None, description="简道云已有跟进记录 _id（更新时优先）")
    data_creator: Optional[str] = Field(None, description="create 时简道云提交人 username")
    # 客户拜访
    account_id: Optional[str] = Field(None, description="简道云客户 _id（crm_accounts.unique_id）")
    customer_code: Optional[str] = Field(None, description="客户编号")
    account_short_name: Optional[str] = Field(None, description="客户简称")
    opportunity_id: Optional[str] = Field(None, description="简道云商机 _id")
    opportunity_name: Optional[str] = Field(None, description="商机名称")
    opportunity_number: Optional[str] = Field(None, description="商机编号")
    # 线索拜访
    lead_id: Optional[str] = Field(None, description="简道云线索 _id（crm_leads.unique_id）")
    lead_serial_number: Optional[str] = Field(None, description="线索流水号")
    # 共用
    account_name: Optional[str] = Field(None, description="客户全称")
    recorder_id: Optional[str] = Field(None, description="跟进人简道云 username（crm_user.unique_id）")
    recorder: Optional[str] = Field(None, description="跟进人显示名")
    visit_communication_date: Optional[str] = Field(None, description="跟进日期（YYYY-MM-DD 或毫秒时间戳）")
    visit_communication_method: Optional[str] = Field(None, description="跟进方式")
    followup_record: Optional[str] = Field(None, description="沟通内容")
    next_steps: Optional[str] = Field(None, description="下一步计划")
    next_followup_time: Optional[str] = Field(None, description="下次跟进时间")
    remarks: Optional[str] = Field(None, description="其他")
    visit_type: Optional[str] = Field(None, description="跟进事项")
    customer_intent: Optional[str] = Field(None, description="客户意向度")
    customer_feedback: Optional[str] = Field(None, description="客户反馈")
    support_needed: Optional[str] = Field(None, description="所需支持")
    visit_record_number: Optional[str] = Field(None, description="跟进记录编号")
    business_type: Optional[str] = Field(None, description="业务空间")
    department: Optional[str] = Field(None, description="Team")


class WebeyeVisitRecordBatchCreateRequest(BaseModel):
    visit_records: List[WebeyeVisitRecordCreateRequest]
    partial_fail: bool = True


class CbgVisitRecordCreateRequest(BaseModel):
    """CBG日常对象创建请求"""
    content: str = Field(..., description="记录内容")
    record_type: str = Field(..., description="跟进类型名称")
    account_ids: Optional[List[str]] = Field(None, description="关联客户ID列表")
    opportunity_ids: Optional[List[str]] = Field(None, description="关联商机ID列表")
    owner_user_id: Optional[str] = Field(None, description="负责人ID")
    source_record_id: Optional[str] = Field(None, description="来源记录ID（用于日志追踪）")

class CbgVisitRecordBatchCreateRequest(BaseModel):
    records: List[CbgVisitRecordCreateRequest]

class CbgVisitRecordType(str, enum.Enum):
    """CBG日常对象跟进类型"""
    CUSTOMER_PHONE = "电话/微信跟进"
    CUSTOMER_VISIT = "常规拜访"
    CUSTOMER_HIGH_LEVEL_VISIT = "高层拜访"
    CUSTOMER_TECHNICAL = "技术交流"
    CUSTOMER_RECENT_DYNAMIC = "最近动态"
    CUSTOMER_FEEDBACK = "用户反馈"
    CUSTOMER_RISK = "风险提示"
    CUSTOMER_NEXT_PLAN = "下阶段计划"