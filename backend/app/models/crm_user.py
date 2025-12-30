from typing import Optional
from sqlmodel import Field, SQLModel

class CRMUser(SQLModel, table=True):
    class Config:
        orm_mode = True
        read_only = True
        
    """用户表"""
    id: Optional[int] = Field(default=None, primary_key=True)
    unique_id: Optional[str] = Field(nullable=True, max_length=64, description="用户ID")
    
    user_name: Optional[str] = Field(nullable=True, max_length=255, description="用户名称")
    department: Optional[str] = Field(nullable=True, max_length=255, description="部门")
    department_id: Optional[str] = Field(nullable=True, max_length=255, description="部门ID")

    __tablename__ = "crm_user"
