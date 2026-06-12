from typing import Optional
from datetime import datetime

from sqlmodel import Field, Column, DateTime, SQLModel, String


class CompanyCompetitor(SQLModel, table=True):
    """竞争对手表"""

    model_config = {"from_attributes": True}

    __tablename__ = "company_competitor"

    id: str = Field(
        sa_column=Column(String(32), primary_key=True, nullable=False),
        description="主键",
    )
    create_by: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32), nullable=True),
        description="创建人",
    )
    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="创建时间",
    )
    update_by: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32), nullable=True),
        description="更新人",
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="更新时间",
    )
    name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(100), nullable=True),
        description="公司名称",
    )
    company_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(50), nullable=True),
        description="公司id",
    )
