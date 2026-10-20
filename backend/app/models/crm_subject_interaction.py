"""业务对象上的一条评论或一条任务关联。

一行一条。kind=comment 是评论，kind=task 是写回的任务编号。
评论用 reply_to_id 盖楼。任务正文在 crm_todos。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class CRMSubjectInteraction(SQLModel, table=True):
    __tablename__ = "crm_subject_interactions"
    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "todo_id",
            name="ux_crm_subject_interactions_todo",
        ),
        Index("ix_crm_subject_interactions_subject", "subject_type", "subject_id", "created_at"),
        Index("ix_crm_subject_interactions_author", "kind", "author_id", "created_at"),
    )

    id: str = Field(primary_key=True, max_length=36, description="记录 id。评论时即评论 id")
    subject_type: str = Field(max_length=64, description="业务对象类型，如 skill_extract_item")
    subject_id: str = Field(max_length=255, description="业务对象 unique_id")
    kind: str = Field(max_length=16, description="comment 评论，task 任务关联")
    scene: str = Field(default="", max_length=64, description="写入这条记录的业务场景")
    reply_to_id: Optional[str] = Field(default=None, max_length=36, description="回复的评论 id，空则为主评论")
    author_id: str = Field(default="", max_length=255, description="操作人用户 id")
    author: str = Field(default="", max_length=255, description="操作人显示名")
    content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True), description="评论正文")
    todo_id: Optional[str] = Field(default=None, max_length=255, description="关联的 crm_todos.unique_id")
    created_at: datetime = Field(description="创建时间")
