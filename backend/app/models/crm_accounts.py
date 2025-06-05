from typing import Optional
from datetime import datetime
from sqlmodel import Field, Column, DateTime, SQLModel

class CRMAccount(SQLModel, table=True):
    class Config:
        orm_mode = True
        read_only = True
        
    """客户表"""
    id: Optional[int] = Field(default=None, primary_key=True)
    unique_id: Optional[str] = Field(nullable=True, max_length=255, description="唯一性ID（必填）")
    customer_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名称（必填）")
    # external_source: Optional[str] = Field(nullable=True, max_length=255, description="外部来源")
    customer_source: Optional[str] = Field(nullable=True, max_length=255, description="客户来源（必填）")
    person_in_charge: Optional[str] = Field(nullable=True, max_length=255, description="负责人")
    department: Optional[str] = Field(nullable=True, max_length=255, description="负责人主属部门")
    customer_level: Optional[str] = Field(nullable=True, max_length=255, description="客户等级")
    industry: Optional[str] = Field(nullable=True, max_length=255, description="客户行业（必填）")
    # industry_level_1: Optional[str] = Field(nullable=True, max_length=255, description="1级行业")
    # industry_level_2: Optional[str] = Field(nullable=True, max_length=255, description="2级行业")
    phone: Optional[str] = Field(nullable=True, max_length=255, description="电话")
    website: Optional[str] = Field(nullable=True, max_length=255, description="网址")
    email: Optional[str] = Field(nullable=True, max_length=255, description="邮件")
    remarks: Optional[str] = Field(nullable=True, description="备注")
    allocation_status: Optional[str] = Field(nullable=True, max_length=255, description="分配状态")
    deal_status: Optional[str] = Field(nullable=True, max_length=255, description="成交状态")
    last_follow_up: Optional[datetime] = Field(nullable=True, description="最后跟进时间")
    last_deal_time: Optional[datetime] = Field(nullable=True, description="最后一次成交时间")
    allocation_time: Optional[datetime] = Field(nullable=True, description="领取/分配时间")
    business_type: Optional[str] = Field(nullable=True, max_length=255, description="业务类型（必填）")
    life_status: Optional[str] = Field(nullable=True, max_length=255, description="生命状态")
    # lock_status: Optional[str] = Field(nullable=True, max_length=255, description="锁定状态")
    belonging_department: Optional[str] = Field(nullable=True, max_length=255, description="归属部门")
    # external_person_in_charge: Optional[str] = Field(nullable=True, max_length=255, description="外部负责人")
    creator: Optional[str] = Field(nullable=True, max_length=255, description="创建人")
    creation_time: Optional[datetime] = Field(nullable=True, description="创建时间")
    last_modifier: Optional[str] = Field(nullable=True, max_length=255, description="最后修改人")
    last_modified_time: Optional[datetime] = Field(nullable=True, description="最后修改时间")
    customer_identifier: Optional[str] = Field(nullable=True, max_length=255, description="客户标识")
    # customer_number: Optional[str] = Field(nullable=True, max_length=255, description="客户编号标示")
    # customer_category: Optional[str] = Field(nullable=True, max_length=255, description="客户分类")
    customer_code: Optional[str] = Field(nullable=True, max_length=255, description="客户编号")
    # project_manager: Optional[str] = Field(nullable=True, max_length=255, description="项目经理负责人")
    # has_person_in_charge: Optional[str] = Field(nullable=True, max_length=255, description="是否有负责人（供交付系统数据传输使用）")
    earliest_deal_date: Optional[datetime] = Field(nullable=True, description="最早成交日期")
    latest_deal_date: Optional[datetime] = Field(nullable=True, description="最新成交日期")
    # is_core_region_customer: Optional[str] = Field(nullable=True, max_length=255, description="是否为核心区域客户")
    # account_status: Optional[str] = Field(nullable=True, max_length=255, description="Account状态")
    # has_ticket_service: Optional[str] = Field(nullable=True, max_length=255, description="是否开通了工单服务")
    # ticket_contact_count: Optional[int] = Field(nullable=True, description="开通工单联系人数量")
    # current_fiscal_year: Optional[str] = Field(nullable=True, max_length=255, description="当前财年")
    customer_abbreviation: Optional[str] = Field(nullable=True, max_length=255, description="客户简称")
    # company_type: Optional[str] = Field(nullable=True, max_length=255, description="公司类型-天眼查")
    # last_follow_up_person: Optional[str] = Field(nullable=True, max_length=255, description="最后跟进人")
    customer_attribute: Optional[str] = Field(nullable=True, max_length=255, description="客户属性")
    # risk_monitoring: Optional[str] = Field(nullable=True, max_length=255, description="是否风险监控")
    # person_in_charge_change_time: Optional[datetime] = Field(sa_column=Column(DateTime(timezone=True), nullable=True), description="负责人变更时间")
    # original_customer_name_unique_id: Optional[str] = Field(nullable=True, max_length=255, description="原客户名称_唯一性ID")
    # original_customer_name: Optional[str] = Field(nullable=True, max_length=255, description="原客户名称")
    # is_named_account: Optional[str] = Field(nullable=True, max_length=255, description="是否 Named Account")

    # 新增字段 - 第一批：基础信息
    partner: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴")
    belonging_pool: Optional[str] = Field(nullable=True, max_length=255, description="所属公海")
    legal_representative: Optional[str] = Field(nullable=True, max_length=255, description="法定代表人")
    
    # 新增字段 - 第二批：地域信息
    country: Optional[str] = Field(nullable=True, max_length=255, description="国家")
    province: Optional[str] = Field(nullable=True, max_length=255, description="省")
    city: Optional[str] = Field(nullable=True, max_length=255, description="市")
    district: Optional[str] = Field(nullable=True, max_length=255, description="区")
    
    # 新增字段 - 第三批：其他信息
    address: Optional[str] = Field(nullable=True, max_length=512, description="详细地址")
    customer_scale_new: Optional[str] = Field(nullable=True, max_length=255, description="客户规模-新")
    first_deal_date: Optional[str] = Field(nullable=True, max_length=255, description="最早成交日期（归档日期）")

    # 备注: 在SQLModel中指定表名
    __tablename__ = "crm_accounts"