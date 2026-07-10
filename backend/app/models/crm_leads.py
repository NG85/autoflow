from typing import Any, Dict, List, Optional
from datetime import datetime
from sqlmodel import Field, Column, SQLModel, Text, JSON


class CRMLead(SQLModel, table=True):
    model_config = {"from_attributes": True}

    """CRM销售线索主表"""
    id: Optional[int] = Field(default=None, primary_key=True)
    unique_id: str = Field(max_length=255, description="上游源系统线索唯一ID")
    lead_name: str = Field(max_length=255, description="线索主题/名称")
    company_name: Optional[str] = Field(nullable=True, max_length=255, description="潜在公司名称")
    remarks: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="备注说明")
    lead_source: Optional[str] = Field(nullable=True, max_length=100, description="线索来源")
    lead_status: str = Field(default="未处理", max_length=50, description="线索状态")
    converted_at: Optional[datetime] = Field(nullable=True, description="线索转化时间")
    converted_by_id: Optional[str] = Field(nullable=True, max_length=64, description="转化操作人ID")
    converted_by_name: Optional[str] = Field(nullable=True, max_length=255, description="转化操作人姓名")
    converted_account_id: Optional[str] = Field(nullable=True, max_length=255, description="转化后生成的客户唯一ID")
    converted_account_name: Optional[str] = Field(nullable=True, max_length=255, description="转化后生成的客户名称")
    converted_opportunity_id: Optional[str] = Field(nullable=True, max_length=255, description="转化后生成的商机唯一ID")
    converted_opportunity_name: Optional[str] = Field(nullable=True, max_length=255, description="转化后生成的商机名称")
    last_followup_at: Optional[datetime] = Field(nullable=True, description="线索最近一次跟进时间")
    owners: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description='线索归属负责人列表，JSON格式: [{"id": "...", "name": "..."}]',
    )
    owner_department_id: Optional[str] = Field(nullable=True, max_length=255, description="线索归属部门ID")
    owner_department_name: Optional[str] = Field(nullable=True, max_length=255, description="线索归属部门名称")
    is_deleted: int = Field(default=0, description="逻辑删除标识（0-正常，1-已删除）")
    extra: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True), description="扩展内容")
    created_at: Optional[datetime] = Field(nullable=True, description="数据落库创建时间")
    updated_at: Optional[datetime] = Field(nullable=True, description="数据最后更新时间")

    __tablename__ = "crm_leads"
