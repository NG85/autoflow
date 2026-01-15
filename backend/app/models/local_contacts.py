from typing import Optional
from datetime import datetime
from uuid import UUID
from sqlmodel import Field, Column, DateTime, Relationship

from app.models.crm_accounts import CRMAccount
from app.models.base import UpdatableBaseModel


class LocalContact(UpdatableBaseModel, table=True):
    """本地联系人信息表（业务系统维护，可增删改查）"""
    
    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增）")
    
    unique_id: str = Field(max_length=255, description="唯一性ID（必填）")
    
    # 基础信息（必填字段）
    name: str = Field(max_length=255, description="联系人姓名（必填）")
    customer_id: str = Field(max_length=255, description="客户ID（关联crm_accounts.unique_id，必填）")
    customer_name: str = Field(max_length=255, description="客户名称（必填）")
    position: str = Field(max_length=255, description="联系人职位/职务（必填）")
    
    # 基础信息（可选字段）
    gender: Optional[str] = Field(nullable=True, max_length=10, description="性别")
    ## 联系方式
    mobile: Optional[str] = Field(nullable=True, max_length=255, description="手机")
    phone: Optional[str] = Field(nullable=True, max_length=255, description="电话")
    email: Optional[str] = Field(nullable=True, max_length=255, description="邮件")
    wechat: Optional[str] = Field(nullable=True, max_length=255, description="微信")
    address: Optional[str] = Field(nullable=True, max_length=512, description="联系地址")
    
    ## 业务信息
    key_decision_maker: Optional[str] = Field(nullable=True, max_length=255, description="关键决策人")
    source: Optional[str] = Field(nullable=True, max_length=255, description="来源")

    ## 备注
    remarks: Optional[str] = Field(nullable=True, description="备注")
    
    # 软删除标记
    delete_flag: Optional[bool] = Field(nullable=True, description="删除标识")
    
    # 审计字段
    created_by: Optional[UUID] = Field(nullable=True, description="创建人ID")
    updated_by: Optional[UUID] = Field(nullable=True, description="最后修改人ID")
    
    # 扩展字段：用于未来回写CRM
    crm_unique_id: Optional[str] = Field(nullable=True, max_length=255, description="CRM系统唯一ID（回写后填充）")
    synced_to_crm: Optional[bool] = Field(nullable=True, description="是否已同步到CRM")
    synced_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), nullable=True),
        description="同步到CRM的时间"
    )
    
    __tablename__ = "local_contacts"
    
    # 关联到客户表
    account: Optional["CRMAccount"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(LocalContact.customer_id==CRMAccount.unique_id)",
            "foreign_keys": "[LocalContact.customer_id]",
            "viewonly": True
        }
    )
