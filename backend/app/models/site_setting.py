from typing import Optional
from datetime import datetime

from sqlmodel import SQLModel, Field, Column, JSON, func, DateTime


class SiteSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=256, unique=True)
    data_type: str = Field(max_length=256)
    value: str = Field(sa_column=Column(JSON))
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            # Microsecond timestamps avoid collisions between successive writes.
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )

    __tablename__ = "site_settings"
