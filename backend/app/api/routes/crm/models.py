from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Annotated, Union
from datetime import date
from pydantic import BaseModel, Field, field_validator
import json
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.core.config import settings

# 定义响应模型
class Opportunity(BaseModel):
    unique_id: str
    opportunity_name: str
    opportunity_type: Optional[str] = None
    owner: Optional[str] = None
    estimated_acv: Optional[int] = None
    opportunity_stage: Optional[str] = None
    forecast_type: Optional[str] = None
    expected_closing_date: Optional[str] = None

class Account(BaseModel):
    unique_id: str
    customer_name: str
    industry: Optional[str] = None
    customer_level: Optional[str] = None
    person_in_charge: Optional[str] = None
    opportunities: List[Opportunity]

# 定义视图类型枚举
class ViewType(str, Enum):
    STANDARD = "standard"  # 标准视图，显示基本字段
    CUSTOM = "custom"      # 自定义视图
    FILTER_OPTIONS = "filter_options"  # 筛选条件选项视图

# 定义客户级别枚举
class CustomerLevel(str, Enum):
    COMMERCIAL = "Commercial"
    ASKTUG_ACCOUNT = "AskTUG Account"
    KEY_ACCOUNT = "Key Account"
    NON_KA = "Non-Ka"
    SKA = "SKA"
    KA = "KA"
    STRATEGIC_ACCOUNT = "Strategic Account"

# 定义商机阶段枚举
class OpportunityStage(str, Enum):
    PROSPECTING = "Prospecting" 
    QUALIFICATION = "Qualification"
    EVALUATION = "Evaluation"
    BIDDING_NEGOTIATING = "Bidding / Negotiating"
    CLOSEDWON = "Closed Won"
    CLOSEDLOST = "Closed Lost"
    CANCEL = "Cancel"

# 定义预测类型枚举
class ForecastType(str, Enum):
    COMMIT = "Commit"
    UPSIDE = "Upside"
    PIPELINE = "Pipeline"
    CLOSEDWON = "Closed Won" 

# 定义商机类型枚举
class OpportunityType(str, Enum):
    NEW = "New"
    EXPANSION = "Expansion"
    RENEW = "Renew"
    RENEWANDEXPANSION = "Renew+Expansion"

# 定义拜访主题枚举
class VisitSubject(str, Enum):
    INITIAL_ENGAGEMENT = "Initial Engagement"
    TECHNICAL_ENGAGEMENT = "Technical Engagement"
    BUSINESS_ENGAGEMENT = "Business Engagement"
    MIXED_ENGAGEMENT = "Mixed Engagement"
    IQM_SCHEDULED = "IQM Scheduled"
    IQM_COMPLETED = "IQM Completed"
    
    @property
    def english(self) -> str:
        """获取英文值"""
        return self.value
    
    @property
    def chinese(self) -> str:
        """获取中文值"""
        chinese_map = {
            "Initial Engagement": "初次接触",
            "Technical Engagement": "技术交流",
            "Business Engagement": "商务洽谈",
            "Mixed Engagement": "混合交流",
            "IQM Scheduled": "IQM已安排",
            "IQM Completed": "IQM已完成"
        }
        return chinese_map.get(self.value, self.value)
    
    @classmethod
    def from_english(cls, english_value: str) -> Optional['VisitSubject']:
        """根据英文值获取枚举"""
        try:
            return cls(english_value)
        except ValueError:
            return None
    
    @classmethod
    def from_chinese(cls, chinese_value: str) -> Optional['VisitSubject']:
        """根据中文值获取枚举"""
        chinese_to_english = {
            "初次接触": "Initial Engagement",
            "技术交流": "Technical Engagement",
            "商务洽谈": "Business Engagement",
            "混合交流": "Mixed Engagement",
            "IQM已安排": "IQM Scheduled",
            "IQM已完成": "IQM Completed"
        }
        english_value = chinese_to_english.get(chinese_value)
        if english_value:
            try:
                return cls(english_value)
            except ValueError:
                return None
        return None

class SL_PULL_IN(str, Enum):
    YES = "是"
    OTHER = "其他"

# 定义记录类型枚举
class RecordType(str, Enum):
    DAILY_SALES_RECORD = "Daily Sales Record"  # 日常销售记录
    CUSTOMER_VISIT = "Customer Visit"  # 客户拜访
    
    @property
    def english(self) -> str:
        """获取英文值"""
        return self.value
    
    @property
    def chinese(self) -> str:
        """获取中文值"""
        chinese_map = {
            "Daily Sales Record": "日常销售记录",
            "Customer Visit": "客户拜访"
        }
        return chinese_map.get(self.value, self.value)
    
    @classmethod
    def from_english(cls, english_value: str) -> Optional['RecordType']:
        """根据英文值获取枚举"""
        try:
            return cls(english_value)
        except ValueError:
            return None
    
    @classmethod
    def from_chinese(cls, chinese_value: str) -> Optional['RecordType']:
        """根据中文值获取枚举"""
        chinese_to_english = {
            "日常销售记录": "Daily Sales Record",
            "客户拜访": "Customer Visit"
        }
        english_value = chinese_to_english.get(chinese_value)
        if english_value:
            try:
                return cls(english_value)
            except ValueError:
                return None
        return None

# 定义过滤操作符枚举
class FilterOperator(str, Enum):
    EQ = "eq"           # 等于
    NEQ = "neq"         # 不等于
    GT = "gt"           # 大于
    GTE = "gte"         # 大于等于
    LT = "lt"           # 小于
    LTE = "lte"         # 小于等于
    IN = "in"           # 包含于列表
    NOT_IN = "not_in"   # 不包含于列表
    LIKE = "like"       # 模糊匹配
    ILIKE = "ilike"     # 不区分大小写的模糊匹配
    IS_NULL = "is_null" # 为空
    NOT_NULL = "not_null" # 不为空
    BETWEEN = "between" # 区间
    NOT = "not"         # 取反

# 定义过滤条件模型
class FilterCondition(BaseModel):
    field: str
    operator: FilterOperator
    value: Optional[Any] = None

# 定义分组条件
class GroupCondition(BaseModel):
    field: str

# 定义 CRM 查询请求
class CrmViewRequest(BaseModel):
    # 视图类型
    view_type: ViewType = ViewType.STANDARD
    
    # 自定义视图的字段（仅当 view_type 为 CUSTOM 时使用）
    custom_fields: Optional[List[str]] = None
    
    # 过滤条件
    filters: List[FilterCondition] = Field(default_factory=list)
    
    # 高级过滤（支持 AND/OR 组合）
    advanced_filters: Optional[Dict[str, Any]] = None
    
    # 排序
    sort_by: Optional[str] = None
    sort_direction: str = "asc"
    
    # 分组
    group_by: Optional[List[GroupCondition]] = None
    
    # 分页
    page: int = 1
    page_size: int = 20

# 字段元数据
class FieldMetadata(BaseModel):
    name: str
    display_name: str
    display_name_en: Optional[str] = None  # 英文显示名称
    type: str
    fixed: bool = True
    filterable: bool = True
    sortable: bool = True
    groupable: bool = False
    description: Optional[str] = None
    default_value: Optional[Any] = None

# 拜访记录附件结构
class VisitAttachment(BaseModel):
    """
    拜访记录附件信息
    
    - 旧版本：attachment 为 base64 字符串或 S3 URL
    - 新版本：使用结构化 JSON，包含图片 URL、经纬度、地址与拍摄时间等信息
    """
    url: Optional[str] = Field(default=None, description="图片地址（如 S3 URL）")
    # 使用 float 类型以兼容数据库中的 DECIMAL 字段和前端传入的字符串（会自动转换）
    latitude: Optional[float] = Field(default=None, description="图片识别出的纬度")
    longitude: Optional[float] = Field(default=None, description="图片识别出的经度")
    location: Optional[str] = Field(default=None, description="图片识别出的地址信息")
    taken_at: Optional[str] = Field(default=None, description="图片拍摄或识别时间")

    @classmethod
    def from_legacy_value(cls, v: Any) -> "VisitAttachment":
        """
        兼容旧格式：
        - 如果是字符串（base64 或 URL），则仅填充 url 字段
        - 如果是 dict，则按字段强制转换
        """
        if v is None or v == "":
            return cls()

        if isinstance(v, cls):
            return v

        # 字符串：可能是 base64 / URL / 已经是 JSON 字符串
        if isinstance(v, str):
            # 优先尝试按 JSON 解析
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    return cls(**parsed)
            except (json.JSONDecodeError, TypeError):
                pass
            # 否则当作 url 处理
            return cls(url=v)

        # 字典：直接按字段构造
        if isinstance(v, dict):
            return cls(**v)

        # 其他类型兜底转成字符串放在 url 里
        return cls(url=str(v))


# 拜访记录公共字段（所有表单类型都包含）
class VisitRecordBase(BaseModel):
    account_name: Optional[str] = None # 客户名称
    account_id: Optional[str] = None # 客户ID
    opportunity_name: Optional[str] = None # 商机名称
    opportunity_id: Optional[str] = None # 商机ID
    partner_name: Optional[str] = None # 合作伙伴名称
    partner_id: Optional[str] = None # 合作伙伴ID
    visit_communication_date: Optional[str] = None # 拜访及沟通日期
    recorder: Optional[str] = None # 记录人
    recorder_id: Optional[str] = None # 记录人ID    
    visit_type: Optional[Literal["form", "link"]] = None # 拜访类型：form(用户填报)、link(非结构化链接/文件)
    visit_url: Optional[str] = None # 会议链接或文件URL
    followup_record: Optional[str] = None # 跟进记录（原文）
    followup_record_zh: Optional[str] = None # 跟进记录（中文版）
    followup_record_en: Optional[str] = None # 跟进记录（英文版）
    followup_quality_level_zh: Optional[str] = None # 跟进质量等级（中文版）
    followup_quality_level_en: Optional[str] = None # 跟进质量等级（英文版）
    followup_quality_reason_zh: Optional[str] = None # 跟进质量原因（中文版）
    followup_quality_reason_en: Optional[str] = None # 跟进质量原因（英文版）
    next_steps: Optional[str] = None # 下一步计划（原文）
    next_steps_zh: Optional[str] = None # 下一步计划（中文版）
    next_steps_en: Optional[str] = None # 下一步计划（英文版）
    next_steps_quality_level_zh: Optional[str] = None # 下一步计划质量等级（中文版）
    next_steps_quality_level_en: Optional[str] = None # 下一步计划质量等级（英文版）
    next_steps_quality_reason_zh: Optional[str] = None # 下一步计划质量原因（中文版）
    next_steps_quality_reason_en: Optional[str] = None # 下一步计划质量原因（英文版）
    attachment: Optional[VisitAttachment] = Field(
        default=None,
        description="附件信息，兼容旧字符串格式（base64 / URL）与新 JSON 结构"
    )
    parent_record: Optional[str] = None # 父记录
    remarks: Optional[str] = None # 备注

    @field_validator("attachment", mode="before")
    @classmethod
    def normalize_attachment(cls, v: Any) -> Any:
        """
        兼容多种入参格式：
        - None / "" -> None
        - 字符串：base64 / URL / JSON 字符串
        - dict：结构化 JSON
        - VisitAttachment：直接返回
        """
        if v is None or v == "":
            return None
        return VisitAttachment.from_legacy_value(v)

# 协同参与人数据结构
class CollaborativeParticipant(BaseModel):
    """协同参与人信息"""
    name: str = Field(description="协同参与人姓名")
    ask_id: Optional[str] = Field(default=None, description="协同参与人ask_id，为空表示非系统注册人员")

# 拜访记录创建请求模型
# 完整版表单
class CompleteVisitRecordCreate(VisitRecordBase):
    form_type: Literal["complete"] = "complete"  # 表单类型标识
    is_first_visit: Optional[bool] = None # 是否首次拜访
    is_call_high: Optional[bool] = None # 是否call high
    contact_position: Optional[str] = None # 客户职位
    contact_name: Optional[str] = None # 客户名字
    visit_communication_method: Optional[str] = None # 拜访及沟通方式
    collaborative_participants: Optional[Union[str, List[CollaborativeParticipant]]] = Field(
        default=None, 
        description="协同参与人，支持字符串格式（向后兼容）或结构化数组格式"
    )
    
    # 备用字段
    customer_lead_source: Optional[str] = None # 客户/线索来源
    visit_object_category: Optional[str] = None # 拜访对象类别
    counterpart_location: Optional[str] = None # 拜访地点
    visit_communication_location: Optional[str] = None # 拜访及沟通地点
    communication_duration: Optional[str] = None # 沟通时长
    expectation_achieved: Optional[str] = None # 是否达成预期
    
    # 动态字段
    record_type: Optional[RecordType] = None  # 记录类型
    visit_purpose: Optional[str] = None  # 拜访目的
    visit_start_time: Optional[str] = None  # 拜访开始时间
    visit_end_time: Optional[str] = None  # 拜访结束时间

    @field_validator('collaborative_participants', mode='before')
    @classmethod
    def validate_collaborative_participants(cls, v):
        """验证并标准化协同参与人字段"""
        if v is None:
            return None
        
        # 如果已经是列表，直接返回
        if isinstance(v, list):
            return v
        
        # 如果是字符串，尝试解析
        if isinstance(v, str):
            # 尝试解析为JSON
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
            
            # 如果不是JSON或解析失败，按原字符串处理
            return v
        
        # 其他类型转换为字符串
        return str(v)
    
    def get_collaborative_participants_list(self) -> List[CollaborativeParticipant]:
        """获取协同参与人列表，自动解析各种格式"""
        if not self.collaborative_participants:
            return []
        
        # 如果已经是列表，直接返回
        if isinstance(self.collaborative_participants, list):
            return self.collaborative_participants
        
        # 如果是字符串，尝试解析
        if isinstance(self.collaborative_participants, str):
            try:
                # 尝试解析为JSON
                parsed = json.loads(self.collaborative_participants)
                if isinstance(parsed, list):
                    # 验证每个元素是否符合CollaborativeParticipant结构
                    participants = []
                    for item in parsed:
                        if isinstance(item, dict):
                            try:
                                participant = CollaborativeParticipant(**item)
                                participants.append(participant)
                            except Exception:
                                # 跳过无效的参与者数据
                                continue
                    return participants
            except (json.JSONDecodeError, TypeError):
                pass
            
            # 如果不是JSON格式，返回空列表（旧格式不支持推送）
            return []
        
        return []

# 简易版表单
class SimpleVisitRecordCreate(VisitRecordBase):
    form_type: Literal["simple"] = "simple"  # 表单类型标识
    # 拜访主题
    subject: Optional[VisitSubject] = Field(None, description="拜访主题")
    followup_content: Optional[str] = None # 跟进内容（简易版表单使用，包含跟进记录和下一步计划）

# 拜访记录联合类型（使用discriminator）
VisitRecordCreate = Annotated[
    SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    Field(discriminator='form_type')
]

# 拜访记录查询请求模型
class VisitRecordQueryRequest(BaseModel):
    # 分页参数
    page: int = 1
    page_size: int = 20
    
    # 过滤条件
    customer_level: Optional[List[str]] = None  # 客户等级（多选）
    account_id: Optional[List[str]] = None  # 客户ID（多选）
    account_name: Optional[List[str]] = None  # 客户名称（多选）
    partner_id: Optional[List[str]] = None  # 合作伙伴ID（多选）
    partner_name: Optional[List[str]] = None  # 合作伙伴名称（多选）
    opportunity_id: Optional[List[str]] = None  # 商机ID（多选）
    opportunity_name: Optional[List[str]] = None  # 商机名称（多选）
    visit_communication_date_start: Optional[str] = None  # 跟进日期开始
    visit_communication_date_end: Optional[str] = None  # 跟进日期结束
    recorder: Optional[List[str]] = None  # 记录人（多选）
    department: Optional[List[str]] = None  # 所在团队（多选）
    visit_communication_method: Optional[List[str]] = None  # 跟进方式（多选）
    visit_purpose: Optional[List[str]] = None  # 拜访目的（多选）
    followup_quality_level: Optional[List[str]] = None  # AI对跟进记录质量评估（多选）
    next_steps_quality_level: Optional[List[str]] = None  # AI对下一步计划质量评估（多选）
    visit_type: Optional[List[str]] = None  # 信息来源（多选）
    subject: Optional[List[str]] = None  # 拜访主题（多选）
    record_type: Optional[List[str]] = None  # 记录类型（多选）
    is_first_visit: Optional[bool] = None  # 是否首次拜访
    is_call_high: Optional[bool] = None  # 是否call high
    last_modified_time_start: Optional[str] = None  # 创建时间开始
    last_modified_time_end: Optional[str] = None  # 创建时间结束
    
    # 排序 - 默认按拜访日期降序
    sort_by: str = "visit_communication_date"  # 排序字段
    sort_direction: str = "desc"  # 排序方向：asc/desc
    language: Optional[str] = None # 语言，只在导出时生效

# 拜访记录响应模型 - 独立的API模型（不参与SQLModel映射）
class VisitRecordResponse(BaseModel):
    id: Optional[int] = Field(default=None, description="主键ID（自增序列）")
    account_name: Optional[str] = Field(default=None, description="客户名称")
    account_id: Optional[str] = Field(default=None, description="客户ID")
    opportunity_name: Optional[str] = Field(default=None, description="商机名称")
    opportunity_id: Optional[str] = Field(default=None, description="商机ID")
    partner_name: Optional[str] = Field(default=None, description="合作伙伴")
    partner_id: Optional[str] = Field(default=None, description="合作伙伴ID")
    customer_lead_source: Optional[str] = Field(default=None, description="客户/线索来源")
    visit_object_category: Optional[str] = Field(default=None, description="拜访对象类别")
    contact_position: Optional[str] = Field(default=None, description="客户职位")
    contact_name: Optional[str] = Field(default=None, description="客户名字")
    recorder: Optional[str] = Field(default=None, description="记录人")
    recorder_id: Optional[str] = Field(default=None, description="记录人ID")
    collaborative_participants: Optional[str] = Field(
        default=None,
        description="协同参与人，TEXT格式存储，支持JSON数组或字符串格式，向后兼容"
    )
    visit_communication_date: Optional[str] = Field(default=None, description="拜访及沟通日期")
    counterpart_location: Optional[str] = Field(default=None, description="拜访地点")
    visit_communication_method: Optional[str] = Field(default=None, description="拜访及沟通方式")
    visit_purpose: Optional[str] = Field(default=None, description="拜访目的")
    communication_duration: Optional[str] = Field(default=None, description="沟通时长")
    expectation_achieved: Optional[str] = Field(default=None, description="是/否达成预期")
    followup_record: Optional[str] = Field(default=None, description="跟进记录")
    followup_record_zh: Optional[str] = Field(default=None, description="跟进记录（中文版）")
    followup_record_en: Optional[str] = Field(default=None, description="跟进记录（英文版）")
    followup_content: Optional[str] = Field(default=None, description="跟进内容（简易版表单使用）")
    followup_quality_level_zh: Optional[str] = Field(default=None, description="跟进记录等级（中文版）")
    followup_quality_level_en: Optional[str] = Field(default=None, description="跟进记录等级（英文版）")
    followup_quality_reason_zh: Optional[str] = Field(default=None, description="跟进记录评判依据")
    followup_quality_reason_en: Optional[str] = Field(default=None, description="跟进记录评判依据（英文版）")
    next_steps: Optional[str] = Field(default=None, description="下一步计划")
    next_steps_zh: Optional[str] = Field(default=None, description="下一步计划（中文版）")
    next_steps_en: Optional[str] = Field(default=None, description="下一步计划（英文版）")
    next_steps_quality_level_zh: Optional[str] = Field(default=None, description="下一步计划等级")
    next_steps_quality_level_en: Optional[str] = Field(default=None, description="下一步计划等级（英文版）")
    next_steps_quality_reason_zh: Optional[str] = Field(default=None, description="下一步计划评判依据")
    next_steps_quality_reason_en: Optional[str] = Field(default=None, description="下一步计划评判依据（英文版）")
    attachment: Optional[VisitAttachment] = Field(default=None, description="原始附件字段（字符串或JSON）")
    parent_record: Optional[str] = Field(default=None, description="父记录")
    remarks: Optional[str] = Field(default=None, description="备注")
    last_modified_time: Optional[str] = Field(default=None, description="最后修改时间")
    record_id: Optional[str] = Field(default=None, description="记录id")
    is_first_visit: Optional[bool] = Field(default=None, description="是否首次拜访")
    is_call_high: Optional[bool] = Field(default=None, description="是否call high")
    visit_type: Optional[str] = Field(default=None, description="拜访类型：form(用户填报)、link(非结构化链接/文件)")
    visit_url: Optional[str] = Field(default=None, description="会议链接或文件URL")
    subject: Optional[str] = Field(default=None, description="拜访主题")
    record_type: Optional[str] = Field(default=None, description="记录类型：Daily Sales Record、Customer Visit")
    visit_start_time: Optional[str] = Field(default=None, description="拜访开始时间")
    visit_end_time: Optional[str] = Field(default=None, description="拜访结束时间")
    latitude: Optional[float] = Field(default=None, description="纬度，范围 -90 到 90")
    longitude: Optional[float] = Field(default=None, description="经度，范围 -180 到 180")

    # 关联字段 - 来自crm_accounts表
    customer_level: Optional[str] = Field(default=None, description="客户等级")
    
    # 关联字段 - 来自user_profiles表
    department: Optional[str] = Field(default=None, description="拜访人所在部门")


    class Config:
        # 允许从ORM / dict 创建，并忽略多余字段
        from_attributes = True
        extra = "ignore"

# 拜访记录查询响应
class VisitRecordQueryResponse(BaseModel):
    items: List[VisitRecordResponse]
    total: int
    page: int
    page_size: int
    pages: int

# 销售个人日报统计数据模型
class BaseReportStatistics(BaseModel):
    """基础报告统计数据"""
    end_customer_total_follow_up: int = Field(description="总跟进最终客户数", ge=0)
    end_customer_total_first_visit: int = Field(description="总首次拜访最终客户数", ge=0)
    end_customer_total_multi_visit: int = Field(description="总多次拜访最终客户数", ge=0)
    partner_total_follow_up: int = Field(description="总跟进合作伙伴数", ge=0)
    partner_total_first_visit: int = Field(description="总首次拜访合作伙伴数", ge=0)
    partner_total_multi_visit: int = Field(description="总多次拜访合作伙伴数", ge=0)
    assessment_red_count: int = Field(description="评估为red的次数", ge=0)
    assessment_yellow_count: int = Field(description="评估为yellow的次数", ge=0)
    assessment_green_count: int = Field(description="评估为green的次数", ge=0)

class DailyReportStatistics(BaseReportStatistics):
    """销售个人日报统计数据"""
    pass

# 团队周报统计数据模型
class WeeklyReportStatistics(BaseReportStatistics):
    """团队周报统计数据"""
    # 平均值字段（字符串类型，因为包含格式化后的数值）
    end_customer_avg_follow_up: str = Field(description="平均跟进最终客户数")
    partner_avg_follow_up: str = Field(description="平均跟进合作伙伴数")

# 基础评估详情模型
class BaseAssessmentDetail(BaseModel):
    """基础评估详情模型"""
    account_name: str = Field(description="客户名称")
    opportunity_names: str = Field(description="商机名称列表，用 | 分隔")
    assessment_flag: str = Field(description="评估标志(🔴/🟡/🟢)")
    assessment_description: str = Field(description="评估描述")
    account_level: str = Field(description="客户等级")
    sales_name: str = Field(description="销售人员姓名")
    department_name: str = Field(description="部门名称")
    
    @classmethod
    def safe_placeholder(cls, value: str) -> str:
        """为空值提供 -- 占位符"""
        if not value or (isinstance(value, str) and value.strip() == ''):
            return "--"
        return value
    
    def __init__(self, **data):
        # 统一处理占位符
        data['account_name'] = self.safe_placeholder(data.get('account_name', ''))
        data['opportunity_names'] = self.safe_placeholder(data.get('opportunity_names', ''))
        data['assessment_description'] = self.safe_placeholder(data.get('assessment_description', ''))
        data['account_level'] = self.safe_placeholder(data.get('account_level', ''))
        data['sales_name'] = self.safe_placeholder(data.get('sales_name', ''))
        data['department_name'] = self.safe_placeholder(data.get('department_name', ''))
        super().__init__(**data)

# 客户评估详情模型（包含跟进记录）
class AssessmentDetail(BaseAssessmentDetail):
    """客户评估详情（包含跟进记录）"""
    follow_up_note: str = Field(description="销售跟进记录")
    follow_up_next_step: str = Field(description="销售跟进下一步")
    
    def __init__(self, **data):
        # 处理跟进记录字段的占位符
        data['follow_up_note'] = self.safe_placeholder(data.get('follow_up_note', ''))
        data['follow_up_next_step'] = self.safe_placeholder(data.get('follow_up_next_step', ''))
        super().__init__(**data)

# 客户评估精简详情模型 - 用于公司日报
class CompanyAssessmentDetail(BaseAssessmentDetail):
    """公司级评估详情（不包含跟进记录）"""
    pass

# 销售个人日报响应模型
class BaseDailyReportResponse(BaseModel):
    """基础日报响应模型"""
    report_date: date = Field(description="报告日期")
    statistics: List[DailyReportStatistics] = Field(description="统计数据")
    visit_detail_page: str = Field(description="拜访记录详情页面链接")
    account_list_page: str = Field(description="客户列表页面链接")
    first_assessment: List[AssessmentDetail] = Field(description="首次拜访评估详情")
    multi_assessment: List[AssessmentDetail] = Field(description="多次拜访评估详情")

class DailyReportResponse(BaseDailyReportResponse):
    """销售个人日报响应"""
    recorder: str = Field(description="记录人/销售人员")
    department_name: str = Field(description="部门名称")

class DepartmentDailyReportResponse(BaseDailyReportResponse):
    """部门日报响应"""
    department_name: str = Field(description="部门名称")

class CompanyDailyReportResponse(BaseModel):
    """公司日报响应"""
    report_date: date = Field(description="报告日期")
    statistics: List[DailyReportStatistics] = Field(description="公司汇总统计数据")
    visit_detail_page: str = Field(description="拜访记录详情页面链接")
    account_list_page: str = Field(description="客户列表页面链接")
    first_assessment: List[CompanyAssessmentDetail] = Field(description="公司首次拜访评估详情汇总")
    multi_assessment: List[CompanyAssessmentDetail] = Field(description="公司多次拜访评估详情汇总")

# 销售四象限分布模型
class SalesQuadrants(BaseModel):
    """销售四象限分布"""
    behavior_hh: List[str] = Field(description="高行为高结果象限的销售人员列表")
    behavior_hl: List[str] = Field(description="高行为低结果象限的销售人员列表")
    behavior_lh: List[str] = Field(description="低行为高结果象限的销售人员列表")
    behavior_ll: List[str] = Field(description="低行为低结果象限的销售人员列表")

# 周报响应模型
class BaseWeeklyReportResponse(BaseModel):
    """基础周报响应模型"""
    report_start_date: date = Field(description="报告开始日期")
    report_end_date: date = Field(description="报告结束日期")
    statistics: List[WeeklyReportStatistics] = Field(description="周报统计数据")
    visit_detail_page: str = Field(description="拜访记录详情页面链接")
    account_list_page: str = Field(description="客户列表页面链接")
    weekly_review_1_page: str = Field(
        description="周报Review1页面链接",
        default_factory=lambda: f"{settings.REVIEW_REPORT_HOST}/review/weeklyDetail/execution_id"
    )
    weekly_review_5_page: str = Field(
        description="周报Review5页面链接", 
        default_factory=lambda: f"{settings.REVIEW_REPORT_HOST}/review/muban5Detail/execution_id"
    )
    sales_quadrants: Optional[SalesQuadrants] = Field(default=None, description="销售四象限分布")

class DepartmentWeeklyReportResponse(BaseWeeklyReportResponse):
    """团队周报响应"""
    department_name: str = Field(description="部门名称")

# 公司周报响应模型
class CompanyWeeklyReportResponse(BaseWeeklyReportResponse):
    """公司周报响应"""
    pass

# 团队周报查询请求
class WeeklyReportRequest(BaseModel):
    """团队周报查询请求"""
    department_name: Optional[str] = Field(default=None, description="部门名称，不传则查询所有部门")
    start_date: Optional[date] = Field(default=None, description="开始日期")
    end_date: Optional[date] = Field(default=None, description="结束日期")

# 销售个人日报查询请求
class DailyReportRequest(BaseModel):
    """销售个人日报查询请求"""
    sales_id: Optional[str] = Field(default=None, description="销售人员ID，不传则查询所有销售")
    sales_name: Optional[str] = Field(default=None, description="销售人员姓名，支持模糊查询")
    start_date: Optional[date] = Field(default=None, description="开始日期")
    end_date: Optional[date] = Field(default=None, description="结束日期")
    department_name: Optional[str] = Field(default=None, description="部门名称过滤")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页大小")

# 客户资料上传请求模型
class CustomerDocumentUploadRequest(BaseModel):
    """客户资料上传请求"""
    file_category: Literal["ABP", "CallHigh", "Other"] = Field(description="文件类别，如ABP、CallHigh、Other等")
    account_name: Optional[str] = Field(default=None, description="客户名称")
    account_id: Optional[str] = Field(default=None, description="客户ID")
    document_url: str = Field(description="文档链接")
    uploader_id: Optional[str] = Field(default=None, description="上传者ID")
    uploader_name: Optional[str] = Field(default=None, description="上传者姓名")
    feishu_auth_code: Optional[str] = Field(default=None, description="飞书授权码")

# 客户资料上传响应模型
class CustomerDocumentUploadResponse(BaseModel):
    """客户资料上传响应"""
    success: bool = Field(description="是否成功")
    message: str = Field(description="响应消息")
    document_id: Optional[int] = Field(default=None, description="文档ID")
    auth_required: Optional[bool] = Field(default=None, description="是否需要授权")
    auth_url: Optional[str] = Field(default=None, description="授权URL")
    auth_expired: Optional[bool] = Field(default=None, description="授权是否过期")
    auth_error: Optional[bool] = Field(default=None, description="授权是否有错误")
    channel: Optional[str] = Field(default=None, description="文档来源渠道")
    document_type: Optional[str] = Field(default=None, description="文档类型")