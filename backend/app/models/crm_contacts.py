from typing import List, Optional
from datetime import datetime, date
from sqlmodel import Field, Column, DateTime, Date, Relationship, SQLModel

from app.models.crm_accounts import CRMAccount

class CRMContact(SQLModel, table=True):
    class Config:
        orm_mode = True
        read_only = True
        
    """联系人信息表"""
    id: Optional[int] = Field(default=None, primary_key=True)
    unique_id: str = Field(max_length=255, description="唯一性ID（必填）")
    name: str = Field(max_length=255, description="联系人姓名（必填）")
    customer_id: Optional[str] = Field(nullable=True, max_length=255, description="客户名称_唯一性ID")
    customer_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名称")
    department1: Optional[str] = Field(nullable=True, max_length=255, description="部门1")
    position1: Optional[str] = Field(nullable=True, max_length=255, description="职务1")
    gender: Optional[str] = Field(nullable=True, max_length=10, description="性别")
    birthday: Optional[date] = Field(sa_column=Column(Date, nullable=True), description="生日")
    key_decision_maker: Optional[str] = Field(nullable=True, max_length=255, description="关键决策人")
    introducer_id: Optional[str] = Field(nullable=True, max_length=255, description="介绍人_唯一性ID")
    introducer: Optional[str] = Field(nullable=True, max_length=255, description="介绍人")
    business_card: Optional[str] = Field(nullable=True, description="名片")
    remarks: Optional[str] = Field(nullable=True, description="备注")
    mobile1: Optional[str] = Field(nullable=True, max_length=255, description="手机")
    mobile2: Optional[str] = Field(nullable=True, max_length=255, description="手机2")
    mobile3: Optional[str] = Field(nullable=True, max_length=255, description="手机3")
    mobile4: Optional[str] = Field(nullable=True, max_length=255, description="手机4")
    mobile5: Optional[str] = Field(nullable=True, max_length=255, description="手机5")
    phone1: Optional[str] = Field(nullable=True, max_length=255, description="电话")
    phone2: Optional[str] = Field(nullable=True, max_length=255, description="电话2")
    phone3: Optional[str] = Field(nullable=True, max_length=255, description="电话3")
    phone4: Optional[str] = Field(nullable=True, max_length=255, description="电话4")
    phone5: Optional[str] = Field(nullable=True, max_length=255, description="电话5")
    email: Optional[str] = Field(nullable=True, max_length=255, description="邮件")
    address: Optional[str] = Field(nullable=True, max_length=255, description="联系地址")
    business_type: str = Field(max_length=255, description="业务类型（必填）")
    life_status: Optional[str] = Field(nullable=True, max_length=255, description="生命状态")
    lock_status: Optional[str] = Field(nullable=True, max_length=255, description="锁定状态")
    responsible_person: str = Field(max_length=255, description="负责人（必填）")
    responsible_department: Optional[str] = Field(nullable=True, max_length=255, description="负责人主属部门")
    affiliate_department: Optional[str] = Field(nullable=True, max_length=255, description="归属部门")
    attitude: Optional[str] = Field(nullable=True, max_length=255, description="态度")
    status: Optional[str] = Field(nullable=True, max_length=255, description="状态")
    department: Optional[str] = Field(nullable=True, max_length=255, description="部门")
    position: Optional[str] = Field(nullable=True, max_length=255, description="职务")
    wechat: Optional[str] = Field(nullable=True, max_length=255, description="微信")
    score: Optional[float] = Field(nullable=True, description="联系人得分")
    mva: Optional[str] = Field(nullable=True, max_length=255, description="MVA")
    contributor: Optional[str] = Field(nullable=True, max_length=255, description="Contributor")
    committer: Optional[str] = Field(nullable=True, max_length=255, description="Committer")
    reviewer: Optional[str] = Field(nullable=True, max_length=255, description="Reviewer")
    service_enabled: Optional[str] = Field(nullable=True, max_length=255, description="是否开通工单服务")
    email_account: Optional[str] = Field(nullable=True, max_length=255, description="邮箱账号")
    contact_status: Optional[str] = Field(nullable=True, max_length=255, description="联系人状态")
    authentication_type: Optional[str] = Field(nullable=True, max_length=255, description="认证类型")
    partner_id: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴_唯一性ID")
    partner: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴")
    external_source: Optional[str] = Field(nullable=True, max_length=255, description="外部来源")
    affiliate_partner_id: Optional[str] = Field(nullable=True, max_length=255, description="所属合作伙伴_唯一性ID")
    affiliate_partner: Optional[str] = Field(nullable=True, max_length=255, description="所属合作伙伴")
    mobile1_city: Optional[str] = Field(nullable=True, max_length=255, description="手机1归属市")
    tidb_knowledge: Optional[str] = Field(nullable=True, max_length=255, description="TiDB 知识掌握情况")
    influence_level: Optional[str] = Field(nullable=True, max_length=255, description="影响力层级")
    position_id: Optional[str] = Field(nullable=True, max_length=255, description="联系人职务_唯一性ID")
    position_name: Optional[str] = Field(nullable=True, max_length=255, description="联系人职务")
    relationship_strength: Optional[str] = Field(nullable=True, max_length=255, description="联系人与我方关系强度")
    mobile1_country: Optional[str] = Field(nullable=True, max_length=255, description="手机1归属国家")
    wechat_user_id: Optional[str] = Field(nullable=True, max_length=255, description="企业微信UserId")
    source: Optional[str] = Field(nullable=True, max_length=255, description="来源")
    mobile1_province: Optional[str] = Field(nullable=True, max_length=255, description="手机1归属省")
    pcta1: Optional[str] = Field(nullable=True, max_length=255, description="PCTA1")
    direct_superior_id: Optional[str] = Field(nullable=True, max_length=255, description="直属上级_唯一性ID")
    direct_superior: Optional[str] = Field(nullable=True, max_length=255, description="直属上级")
    pctp1: Optional[str] = Field(nullable=True, max_length=255, description="PCTP1")
    affiliate_partner_department_id: Optional[str] = Field(nullable=True, max_length=255, description="所属合作伙伴部门_唯一性ID")
    affiliate_partner_department: Optional[str] = Field(nullable=True, max_length=255, description="所属合作伙伴部门")
    tidb_knowledge1: Optional[str] = Field(nullable=True, max_length=255, description="TiDB 知识掌握情况1")
    user_read_only: Optional[str] = Field(nullable=True, max_length=255, description="人员-普通成员-只读")
    user_read_write: Optional[str] = Field(nullable=True, max_length=255, description="人员-普通成员-读写")
    department_read_only: Optional[str] = Field(nullable=True, max_length=255, description="部门-普通成员-只读")
    department_read_write: Optional[str] = Field(nullable=True, max_length=255, description="部门-普通成员-读写")
    user_group_read_only: Optional[str] = Field(nullable=True, max_length=255, description="用户组-普通成员-只读")
    user_group_read_write: Optional[str] = Field(nullable=True, max_length=255, description="用户组-普通成员-读写")
    role_read_only: Optional[str] = Field(nullable=True, max_length=255, description="角色-普通成员-只读")
    role_read_write: Optional[str] = Field(nullable=True, max_length=255, description="角色-普通成员-读写")
    created_by: Optional[str] = Field(nullable=True, max_length=255, description="创建人")
    created_date: Optional[datetime] = Field(sa_column=Column(DateTime(timezone=True), nullable=True), description="创建时间")
    last_modified_by: Optional[str] = Field(nullable=True, max_length=255, description="最后修改人")
    last_modified_date: Optional[datetime] = Field(sa_column=Column(DateTime(timezone=True), nullable=True), description="最后修改时间")

    __tablename__ = "crm_contacts"
    
    account: Optional["CRMAccount"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(CRMContact.customer_id==cast(CRMAccount.unique_id, String))",
            "foreign_keys": "[CRMContact.customer_id]",
            "viewonly": True
        }
    )
    
    # 添加直属上级关系
    superior: Optional["CRMContact"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(CRMContact.direct_superior_id==cast(CRMContact.unique_id, String))",
            "foreign_keys": "[CRMContact.direct_superior_id]",
            "viewonly": True,
            "remote_side": "[CRMContact.unique_id]"
        }
    )
    
    # 添加下属关系(反向关系)
    subordinates: List["CRMContact"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(remote(cast(CRMContact.unique_id, String))==foreign(CRMContact.direct_superior_id))",
            "viewonly": True,
            "overlaps": "superior"
        }
    )