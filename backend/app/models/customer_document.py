from datetime import datetime
from typing import Optional, TYPE_CHECKING
from uuid import UUID
from sqlmodel import Field, SQLModel, Relationship
from sqlmodel.sql.sqltypes import GUID

if TYPE_CHECKING:
    from app.models.document_contents import DocumentContent


class CustomerDocument(SQLModel, table=True):
    """客户文档表"""
    __tablename__ = "customer_documents"
    
    id: int = Field(primary_key=True, description="自增主键ID")
    file_category: str = Field(description="文件类别，如ABP、CallHigh等")
    account_name: str = Field(description="客户名称")
    account_id: str = Field(description="客户ID")
    document_url: str = Field(description="文档链接")
    document_type: Optional[str] = Field(default=None, description="文档类型")
    document_title: Optional[str] = Field(default=None, description="文档标题")
    uploader_id: UUID = Field(sa_type=GUID, foreign_key="users.id", description="上传者ID")
    uploader_name: str = Field(description="上传者姓名")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    
    # 关联的文档内容ID
    document_content_id: Optional[int] = Field(default=None, foreign_key="document_contents.id", description="关联的文档内容ID")
    
    # 关联文档内容表（只读关联，用于获取文档内容信息）
    document_content: Optional["DocumentContent"] = Relationship()
    
    class Config:
        arbitrary_types_allowed = True
