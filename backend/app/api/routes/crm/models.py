from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from datetime import date
from pydantic import BaseModel, Field
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
    is_first_visit: Optional[bool] = None # 是否首次拜访
    is_call_high: Optional[bool] = None # 是否call high
    account_name: Optional[str] = None # 客户名称
    account_id: Optional[str] = None # 客户ID
    opportunity_name: Optional[str] = None # 商机名称
    opportunity_id: Optional[str] = None # 商机ID
    partner_name: Optional[str] = None # 合作伙伴名称
    customer_lead_source: Optional[str] = None # 客户/线索来源
    visit_communication_date: Optional[str] = None # 拜访及沟通日期
    visit_object_category: Optional[str] = None # 拜访对象类别
    contact_position: Optional[str] = None # 客户职位
    contact_name: Optional[str] = None # 客户名字
    recorder: Optional[str] = None # 记录人
    recorder_id: Optional[str] = None # 记录人ID
    counterpart_location: Optional[str] = None # 拜访地点
    visit_communication_method: Optional[str] = None # 拜访及沟通方式
    # visit_communication_location: Optional[str] = None # 拜访及沟通地点
    communication_duration: Optional[str] = None # 沟通时长
    expectation_achieved: Optional[str] = None # 是否达成预期
    collaborative_participants: Optional[str] = None # 协同参与人
    followup_record: Optional[str] = None # 跟进记录
    next_steps: Optional[str] = None # 下一步计划
    followup_quality_level: Optional[str] = None # 跟进质量等级
    followup_quality_reason: Optional[str] = None # 跟进质量原因
    next_steps_quality_level: Optional[str] = None # 下一步计划质量等级
    next_steps_quality_reason: Optional[str] = None # 下一步计划质量原因
    attachment: Optional[str] = None # 附件
    parent_record: Optional[str] = None # 父记录
    remarks: Optional[str] = None # 备注
    visit_type: Optional[Literal["form", "link"]] = None # 拜访类型：form(用户填报)、link(非结构化链接/文件)
    visit_url: Optional[str] = None # 会议链接或文件URL

# 拜访记录查询请求模型
class VisitRecordQueryRequest(BaseModel):
    # 分页参数
    page: int = 1
    page_size: int = 20
    
    # 过滤条件
    customer_level: Optional[List[str]] = None  # 客户等级（多选）
    account_id: Optional[List[str]] = None  # 客户ID（多选）
    account_name: Optional[List[str]] = None  # 客户名称（多选）
    partner_name: Optional[List[str]] = None  # 合作伙伴（多选）
    visit_communication_date_start: Optional[str] = None  # 跟进日期开始
    visit_communication_date_end: Optional[str] = None  # 跟进日期结束
    recorder: Optional[List[str]] = None  # 记录人（多选）
    department: Optional[List[str]] = None  # 所在团队（多选）
    visit_communication_method: Optional[List[str]] = None  # 跟进方式（多选）
    followup_quality_level: Optional[List[str]] = None  # AI对跟进记录质量评估（多选）
    next_steps_quality_level: Optional[List[str]] = None  # AI对下一步计划质量评估（多选）
    visit_type: Optional[List[str]] = None  # 信息来源（多选）
    is_first_visit: Optional[bool] = None  # 是否首次拜访
    is_call_high: Optional[bool] = None  # 是否call high
    
    # 排序 - 默认按拜访日期降序
    sort_by: str = "visit_communication_date"  # 排序字段
    sort_direction: str = "desc"  # 排序方向：asc/desc

# 拜访记录响应模型 - 直接继承CRMSalesVisitRecord，添加关联字段
class VisitRecordResponse(CRMSalesVisitRecord):
    # 重写UUID字段为字符串类型
    recorder_id: Optional[str] = None
    
    # 重写日期字段为字符串类型
    visit_communication_date: Optional[str] = None
    last_modified_time: Optional[str] = None
    
    # 关联字段 - 来自crm_accounts表
    customer_level: Optional[str] = None  # 客户等级
    
    # 关联字段 - 来自user_profiles表
    department: Optional[str] = None  # 拜访人所在部门
    
    class Config:
        # 允许从ORM模型创建
        from_attributes = True

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
    file_category: Literal["ABP", "CallHigh"] = Field(description="文件类别，如ABP、CallHigh等")
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