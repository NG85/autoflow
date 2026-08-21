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
    lead_ids: Optional[List[str]] = Field(None, description="关联线索ID列表")
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


class FenbeitongVisitRecordType(str, enum.Enum):
    """分贝通跟进记录类型（网关约定）"""

    QUICK = "快速记录"
    FACE_VISIT = "面访"
    PHONE = "电话"
    REMOTE_MEETING = "远程会议"
    CONTACT_ANNOTATION = "联系方式标注"


class FenbeitongVisitRecordCreateRequest(BaseModel):
    """分贝通跟进记录创建请求"""

    source_record_id: Optional[str] = Field(None, description="来源记录ID（用于日志追踪）")
    content: str = Field(..., description="跟进内容")
    record_type: str = Field(..., description="跟进类型：快速记录/面访/电话/远程会议/联系方式标注")
    account_ids: Optional[List[str]] = Field(None, description="关联客户ID列表")
    opportunity_ids: Optional[List[str]] = Field(None, description="关联商机ID列表")
    owner_user_id: Optional[str] = Field(None, description="负责人 CRM 用户ID（FSUID）")
    checkin_id: Optional[str] = Field(None, description="关联外勤打卡ID（可选）")


class FenbeitongVisitRecordBatchCreateRequest(BaseModel):
    records: List[FenbeitongVisitRecordCreateRequest]


class LiepinVisitContact(BaseModel):
    """猎聘拜访回写联系人（``contacts`` 数组元素）。"""

    contact_id: Optional[str] = Field(None, description="联系人 unique_id")
    name: Optional[str] = Field(None, description="联系人姓名")
    position: Optional[str] = Field(None, description="联系人职位")


class LiepinVisitRecordCreateRequest(BaseModel):
    """猎聘拜访记录回写请求（``POST /crm-liepin/visit-record``）。"""

    record_id: str = Field(..., description="拜访/跟进唯一标识")
    followup_object_type: Optional[str] = Field(
        None, description="跟进对象类型：end_customer / lead / partner，默认 end_customer"
    )
    followup_object_id: str = Field(..., description="客户/线索 unique_id")
    followup_object_name: str = Field(..., description="客户名或线索名")
    opportunity_id: Optional[str] = Field(None, description="关联商机 unique_id（有关联时必传）")
    recorder: Optional[str] = Field(None, description="记录人姓名")
    recorder_id: Optional[str] = Field(None, description="crm_user_id，如 FSUID_xxx")
    visit_communication_date: str = Field(..., description="拜访/沟通日期 YYYY-MM-DD")
    visit_communication_method: str = Field(
        ...,
        description="拜访方式：实地拜访 / 实地转远程拜访 / 远程视频会议 / 视频会议转电话拜访",
    )
    followup_record: str = Field(..., description="跟进记录")
    next_steps: str = Field(..., description="下一步")
    last_modified_time: str = Field(
        ..., description="最后修改时间（UTC），如 2025-07-15 02:27:48"
    )
    contacts: Optional[List[LiepinVisitContact]] = Field(
        None, description="被拜访联系人数组；元素含 contact_id / name / position"
    )
    contact_id: Optional[List[str]] = Field(
        None,
        description="联系人 ID 集合；有 contacts 时可从中提取，转发明文时一并附带",
    )
    visit_url: Optional[str] = Field(None, description="会议链接或文件URL")


class LiepinVisitRecordBatchCreateRequest(BaseModel):
    """猎聘拜访记录批量回写请求（``POST /crm-liepin/visit-record/batch``）。"""

    visits: List[LiepinVisitRecordCreateRequest]
    partial_fail: bool = True


class WywjVisitRecordCreateRequest(BaseModel):
    """网眼云捷（飞书）拜访记录回写请求（``POST /crm-feishu/wywj/visit-record``）。"""

    record_id: Optional[str] = Field(None, description="业务幂等键（与 source_record_id 二选一）")
    source_record_id: Optional[str] = Field(None, description="业务幂等键（与 record_id 二选一）")
    followup_object_id: Optional[str] = Field(
        None, description="客户/线索飞书行 id（rec...）"
    )
    opportunity_id: Optional[str] = Field(None, description="商机飞书行 id（rec...）")
    recorder_id: Optional[str] = Field(None, description="跟进人飞书 open_id（ou_...）")
    collaborative_participants: Optional[List[str]] = Field(
        None, description="内部参与人飞书 open_id（ou_...）数组"
    )
    visit_communication_date: Optional[str] = Field(
        None, description="跟进日期，如 2026-07-28"
    )
    contact_name: Optional[str] = Field(None, description="沟通对象文本")
    followup_record: Optional[str] = Field(None, description="跟进正文")
    next_steps: Optional[str] = Field(None, description="下一步计划")


class WywjVisitRecordBatchCreateRequest(BaseModel):
    """网眼云捷拜访记录批量回写请求（``POST /crm-feishu/wywj/visit-record/batch``）。"""

    visit_records: List[WywjVisitRecordCreateRequest]
    partial_fail: bool = True