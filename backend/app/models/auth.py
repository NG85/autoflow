from pydantic import EmailStr
from sqlmodel import Field

from app.models.base import UpdatableBaseModel, UUIDBaseModel


class User(UUIDBaseModel, UpdatableBaseModel, table=True):
    email: EmailStr = Field(index=True, unique=True, nullable=False)
    hashed_password: str
    is_active: bool = Field(True, nullable=False)
    is_superuser: bool = Field(False, nullable=False)
    is_verified: bool = Field(False, nullable=False)

    __tablename__ = "users"
