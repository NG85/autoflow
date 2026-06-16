from typing import Any, Optional

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field

from app.models.base import UUIDBaseModel, UpdatableBaseModel


class CRMSalesVisitRecordRevision(UUIDBaseModel, UpdatableBaseModel, table=True):
    """crm_sales_visit_records 修订审计（一次 PATCH 对应一行）。"""

    __tablename__ = "crm_sales_visit_records_revisions"

    record_id: str = Field(max_length=100, description="拜访记录业务 ID（crm_sales_visit_records.record_id）")
    revision_seq: int = Field(description="同 record 递增序号")
    revised_by_id: str = Field(max_length=64, description="修订人 user_id")
    revised_by_name: Optional[str] = Field(default=None, max_length=255, description="修订人姓名快照")
    changes: list[dict[str, Any]] = Field(sa_column=Column(JSON, nullable=False))
    aldebaran_message_type: str = Field(max_length=128)
    aldebaran_dedupe_key: str = Field(max_length=255)
    card_push_status: Optional[str] = Field(default=None, max_length=32)

    __table_args__ = (
        Index("idx_crm_sales_visit_records_revisions_record_id", "record_id"),
        Index(
            "idx_crm_sales_visit_records_revisions_record_seq",
            "record_id",
            "revision_seq",
            unique=True,
        ),
    )
