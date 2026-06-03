from typing import Any, Dict, List, Optional
from datetime import datetime
from sqlmodel import Field, Column, SQLModel, Text, JSON


class CRMAccount(SQLModel, table=True):
    model_config = {"from_attributes": True}

    """客户表"""
    id: Optional[int] = Field(default=None, primary_key=True)
    unique_id: Optional[str] = Field(nullable=True, max_length=255, description="唯一性ID（必填）")
    customer_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名称（必填）")
    customer_source: Optional[str] = Field(nullable=True, max_length=255, description="客户来源（必填）")
    person_in_charge: Optional[str] = Field(nullable=True, max_length=255, description="负责人")
    department: Optional[str] = Field(nullable=True, max_length=255, description="负责人主属部门")
    customer_level: Optional[str] = Field(nullable=True, max_length=255, description="客户等级")
    industry: Optional[str] = Field(nullable=True, max_length=255, description="客户行业（必填）")
    phone: Optional[str] = Field(nullable=True, max_length=255, description="电话")
    website: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="网址")
    email: Optional[str] = Field(nullable=True, max_length=255, description="邮件")
    remarks: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="备注")
    allocation_status: Optional[str] = Field(nullable=True, max_length=255, description="分配状态")
    deal_status: Optional[str] = Field(nullable=True, max_length=255, description="成交状态")
    last_follow_up: Optional[datetime] = Field(nullable=True, description="最后跟进时间")
    last_deal_time: Optional[datetime] = Field(nullable=True, description="最后一次成交时间")
    allocation_time: Optional[datetime] = Field(nullable=True, description="领取/分配时间")
    business_type: Optional[str] = Field(nullable=True, max_length=255, description="业务类型（必填）")
    life_status: Optional[str] = Field(nullable=True, max_length=255, description="生命状态")
    belonging_department: Optional[str] = Field(nullable=True, max_length=255, description="归属部门")
    creator: Optional[str] = Field(nullable=True, max_length=255, description="创建人")
    creation_time: Optional[datetime] = Field(nullable=True, description="创建时间")
    last_modifier: Optional[str] = Field(nullable=True, max_length=255, description="最后修改人")
    last_modified_time: Optional[datetime] = Field(nullable=True, description="最后修改时间")
    customer_identifier: Optional[str] = Field(nullable=True, max_length=255, description="客户标识")
    customer_code: Optional[str] = Field(nullable=True, max_length=255, description="客户编号")
    earliest_deal_date: Optional[datetime] = Field(nullable=True, description="最早成交日期")
    latest_deal_date: Optional[datetime] = Field(nullable=True, description="最新成交日期")
    customer_abbreviation: Optional[str] = Field(nullable=True, max_length=255, description="客户简称")
    customer_attribute: Optional[str] = Field(nullable=True, max_length=255, description="客户属性")
    partner: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴")
    belonging_pool: Optional[str] = Field(nullable=True, max_length=255, description="所属公海")
    legal_representative: Optional[str] = Field(nullable=True, max_length=255, description="法定代表人")
    country: Optional[str] = Field(nullable=True, max_length=255, description="国家")
    province: Optional[str] = Field(nullable=True, max_length=255, description="省")
    city: Optional[str] = Field(nullable=True, max_length=255, description="市")
    district: Optional[str] = Field(nullable=True, max_length=255, description="区")
    address: Optional[str] = Field(nullable=True, max_length=512, description="详细地址")
    customer_scale_new: Optional[str] = Field(nullable=True, max_length=255, description="客户规模-新")
    first_deal_date: Optional[str] = Field(nullable=True, max_length=255, description="最早成交日期（归档日期）")
    delete_flag: Optional[int] = Field(default=0, nullable=True, description="删除标识（0-正常，1-已删除）")
    account_level: Optional[str] = Field(nullable=True, max_length=255, description="Name Account 分级")
    key_actions: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="top 3 key action")
    daily_followup: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="客户日常跟进")
    account_type: Optional[str] = Field(nullable=True, max_length=255, description="实体类型")
    extra: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True), description="扩展内容")
    billing_country: Optional[str] = Field(nullable=True, max_length=255)
    # 忽略原始值字段
    # sf_raw: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    client_id: Optional[str] = Field(nullable=True, max_length=255)
    support_person: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="辅助人员列表，JSON格式存储多个辅助人员的userid和name",
    )
    status: Optional[str] = Field(
        nullable=True,
        max_length=255,
        description='客户状态（"未生效"、"审核中"、"活跃"、"已废弃"）',
    )
    person_in_charge_id: Optional[str] = Field(
        nullable=True,
        max_length=255,
        description="负责人ID（对应crm_user.unique_id）",
    )

    __tablename__ = "crm_accounts"
