"""
CRM 部门镜像模型 - 本地同步 CRM 系统的部门数据
"""
from typing import Optional
from sqlmodel import Field, SQLModel

class DepartmentMirror(SQLModel, table=True):
    """CRM 部门镜像表 - 本地同步 CRM 系统的部门数据，仅用于查询"""

    __tablename__ = "department_mirror"

    unique_id: str = Field(
        max_length=100,
        primary_key=True,
        nullable=False,
        index=True,
        description="CRM 部门唯一ID",
    )
    
    department_name: str = Field(
        max_length=255,
        nullable=False,
        description="部门名称",
    )
    
    parent_id: Optional[str] = Field(
        max_length=100,
        nullable=True,
        index=True,
        description="父部门ID",
    )
    
    path: Optional[str] = Field(
        max_length=500,
        nullable=True,
        index=True,
        description="部门层级路径",
    )
        
    is_active: bool = Field(
        default=True,
        nullable=False,
        description="是否有效",
    )
    
    description: Optional[str] = Field(
        max_length=500,
        nullable=True,
        description="描述",
    )

    def __repr__(self):
        return f"<DepartmentMirror(unique_id='{self.unique_id}', department_name='{self.department_name}', parent_id='{self.parent_id}')>"
