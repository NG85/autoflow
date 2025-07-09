from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

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

class SL_PULL_IN(str, Enum):
    YES = "是"
    OTHER = "其他"

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
    type: str
    fixed: bool = True
    filterable: bool = True
    sortable: bool = True
    groupable: bool = False
    description: Optional[str] = None
    default_value: Optional[Any] = None

# 拜访记录创建请求
class VisitRecordCreate(BaseModel):
    account_name: str # 客户名称
    account_id: Optional[str] = None # 客户ID
    customer_lead_source: str # 客户/线索来源
    visit_communication_date: str # 拜访及沟通日期
    visit_object_category: str # 拜访对象类别
    contact_position: str # 客户职位
    contact_name: str # 客户名字
    recorder: str # 记录人
    counterpart_location: str # 对方所在地
    visit_communication_method: str # 拜访及沟通方式
    visit_communication_location: Optional[str] = None # 拜访及沟通地点
    communication_duration: str # 沟通时长
    expectation_achieved: str # 是否达成预期
    collaborative_participants: Optional[str] = None # 协调参与人
    followup_record: str # 跟进记录
    attachment: Optional[str] = None # 附件
    parent_record: Optional[str] = None # 父记录
    next_steps: Optional[str] = None # 下一步计划