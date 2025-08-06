from datetime import datetime
from typing import Optional
from uuid import UUID
from sqlmodel import SQLModel, Field, Column, Text, DateTime, func, Index, Relationship as SQLRelationship
from sqlalchemy.dialects.mysql import LONGTEXT


class DocumentContent(SQLModel, table=True):
    """文档原文存储表"""
    __tablename__ = "document_contents"

    id: Optional[int] = Field(default=None, primary_key=True, description="自增主键ID")
    user_id: UUID = Field(foreign_key="users.id", nullable=False, description="用户ID")
    user: "User" = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "DocumentContent.user_id == User.id",
        },
    )
    
    # 关联信息
    visit_record_id: Optional[str] = Field(max_length=128, nullable=True, description="关联的拜访记录ID")
    
    # 文档基本信息
    document_type: str = Field(max_length=50, nullable=False, description="文档类型: feishu_doc, feishu_minute, file")
    source_url: str = Field(sa_column=Column(Text, nullable=False), description="原文档URL")
    
    # 核心存储字段
    raw_content: str = Field(sa_column=Column(LONGTEXT, nullable=False), description="原始文档内容")
    
    # 元数据
    title: Optional[str] = Field(max_length=500, nullable=True, description="文档标题")
    file_size: Optional[int] = Field(nullable=True, description="文件大小(字节)")
    
    # 时间戳
    created_at: datetime = Field(sa_column=Column(DateTime, server_default=func.current_timestamp(), nullable=False), description="创建时间")
    updated_at: datetime = Field(sa_column=Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=False), description="更新时间")
    
    __table_args__ = (
        Index("idx_user_id", "user_id"),
        Index("idx_document_type", "document_type"),
        Index("idx_visit_record_id", "visit_record_id"),
        Index("idx_created_at", "created_at"),
        Index("idx_user_type", "user_id", "document_type"),
    ) 