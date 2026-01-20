import enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator

from app.models.data_source import DataSourceType


class RequiredConfigStatus(BaseModel):
    default_llm: bool
    default_embedding_model: bool
    default_chat_engine: bool
    knowledge_base: bool


class OptionalConfigStatus(BaseModel):
    langfuse: bool
    default_reranker: bool


class NeedMigrationStatus(BaseModel):
    chat_engines_without_kb_configured: list[int]


class SystemConfigStatusResponse(BaseModel):
    required: RequiredConfigStatus
    optional: OptionalConfigStatus
    need_migration: NeedMigrationStatus


class TosUploadConfig(BaseModel):
    name: str
    size: int
    path: str
    mime_type: str
        

class NotifyTosUploadRequest(BaseModel):
    name: str
    data_source_type: DataSourceType
    config: List[TosUploadConfig]
    meta: dict


class ChatMode(str, enum.Enum):
    CREATE_CVG_REPORT = "create_cvg_report"
    SAVE_CVG_REPORT = "save_cvg_report"
    CVG_CHAT = "cvg_chat"
    DEFAULT = "default"


# Local Contact Models
class LocalContactCreate(BaseModel):
    """创建本地联系人请求模型"""
    name: str = Field(..., description="联系人姓名（必填）")
    customer_id: str = Field(..., description="客户ID（必填）")
    customer_name: str = Field(..., description="客户名称（必填）")
    position: str = Field(..., description="联系人职位/职务（必填）")
    gender: Optional[str] = Field(None, description="性别")
    mobile: Optional[str] = Field(None, description="手机")
    phone: Optional[str] = Field(None, description="电话")
    email: Optional[str] = Field(None, description="邮件")
    wechat: Optional[str] = Field(None, description="微信")
    address: Optional[str] = Field(None, description="联系地址")
    key_decision_maker: bool = Field(default=False, description="是否关键决策人")
    department: Optional[str] = Field(None, description="部门")
    direct_superior: Optional[str] = Field(None, description="直属上级")
    status: Optional[str] = Field(None, description="在职状态")
    source: Optional[str] = Field(None, description="来源")
    business_relationship: Optional[str] = Field(None, description="商务关系")
    remarks: Optional[str] = Field(None, description="备注")
    
    @model_validator(mode='after')
    def validate_contact_info(self):
        """验证手机和电话至少有一个"""
        mobile = (self.mobile or "").strip()
        phone = (self.phone or "").strip()
        if not mobile and not phone:
            raise ValueError("手机和电话至少需要填写一个")
        return self


class LocalContactUpdate(BaseModel):
    """更新本地联系人请求模型"""
    name: Optional[str] = Field(None, description="联系人姓名")
    position: Optional[str] = Field(None, description="职务")
    gender: Optional[str] = Field(None, description="性别")
    mobile: Optional[str] = Field(None, description="手机")
    phone: Optional[str] = Field(None, description="电话")
    email: Optional[str] = Field(None, description="邮件")
    wechat: Optional[str] = Field(None, description="微信")
    address: Optional[str] = Field(None, description="联系地址")
    key_decision_maker: Optional[bool] = Field(None, description="是否关键决策人")
    department: Optional[str] = Field(None, description="部门")
    direct_superior: Optional[str] = Field(None, description="直属上级")
    status: Optional[str] = Field(None, description="在职状态")
    source: Optional[str] = Field(None, description="来源")
    business_relationship: Optional[str] = Field(None, description="商务关系")
    remarks: Optional[str] = Field(None, description="备注")


class LocalContactResponse(BaseModel):
    """本地联系人响应模型"""
    id: int
    unique_id: Optional[str] = None
    name: str
    customer_id: str
    customer_name: Optional[str] = None
    position: Optional[str] = None
    gender: Optional[str] = None
    mobile: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    wechat: Optional[str] = None
    address: Optional[str] = None
    key_decision_maker: Optional[bool] = None
    department: Optional[str] = None
    direct_superior: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    business_relationship: Optional[str] = None
    remarks: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    crm_unique_id: Optional[str] = None
    synced_to_crm: bool = False
    synced_at: Optional[str] = None
    is_existing: Optional[bool] = Field(default=None, description="是否为已存在的联系人（创建时返回）")
    
    class Config:
        from_attributes = True

