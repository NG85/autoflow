from datetime import datetime
from typing import Iterable, Optional
from uuid import UUID
from sqlmodel import Field, SQLModel
from sqlalchemy import UniqueConstraint

from app.platforms.constants import SUPPORTED_PLATFORMS


class UserOAuthAccount(SQLModel, table=True):
    """
    第三方登录账号映射表
    
    注意：此表在其他系统维护，本系统只能读取，不能进行写操作（INSERT/UPDATE/DELETE）
    """
    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "open_id",
            "user_id_in_platform",
            name="uq_oauth_user",
        ),
    )
    
    uid: str = Field(
        default=None,
        primary_key=True,
        index=True,
        nullable=False,
        description="主键ID"
    )
    
    # 外键关联用户表
    user_id: UUID = Field(
        nullable=False,
        index=True,
        description="系统用户表 users.id（UUID 对象，实际以无连字符的 CHAR(32) 存储）"
    )
    
    # OAuth提供商信息
    provider: str = Field(
        max_length=50,
        nullable=False,
        index=True,
        description="OAuth提供商：feishu, dingtalk, wecom, github, google等"
    )
    
    # OAuth标识信息
    open_id: Optional[str] = Field(
        default=None,
        max_length=255,
        index=True,
        description="开放ID"
    )
    
    union_id: Optional[str] = Field(
        default=None,
        max_length=255,
        index=True,
        description="统一ID"
    )
    
    user_id_in_platform: Optional[str] = Field(
        default=None,
        max_length=255,
        index=True,
        description="钉钉/飞书内部用户ID"
    )
    
    password: Optional[str] = Field(
        default=None,
        max_length=100,
        description="用户密码，适用于系统内账号密码注册"
    )
    
    # 企业相关字段
    tenant_key: Optional[str] = Field(
        default=None,
        max_length=255,
        description="租户密钥"
    )
    
    corporation_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="企业ID"
    )

    create_time: Optional[datetime] = Field(
        default=None,
        description="创建时间（由其他系统维护）",
    )
    update_time: Optional[datetime] = Field(
        default=None,
        description="更新时间（由其他系统维护）",
    )
        
    def __repr__(self):
        return f"<UserOAuthAccount(uid={self.uid}, user_id={str(self.user_id)}, provider='{self.provider}', open_id='{self.open_id}', union_id='{self.union_id}')>"


def _oauth_account_sort_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        return datetime.min
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def oauth_push_receive_id(account: Optional["UserOAuthAccount"]) -> str:
    """推送用的平台收件人 ID。

    飞书 / 钉钉用 open_id。企微账号现网 open_id 常为空，userid 在
    user_id_in_platform（其次 union_id），作 Sia bot 的 chatid。
    """
    if account is None:
        return ""
    open_id = (getattr(account, "open_id", None) or "").strip()
    if open_id:
        return open_id
    provider = (getattr(account, "provider", None) or "").strip().lower()
    if provider == "wecom":
        for attr in ("user_id_in_platform", "union_id"):
            value = (getattr(account, attr, None) or "").strip()
            if value:
                return value
    return ""


def is_deliverable_oauth_account(account: Optional["UserOAuthAccount"]) -> bool:
    """是否可用于推送：受支持平台且有可送达的收件人 ID。"""
    if account is None:
        return False
    provider = getattr(account, "provider", None)
    return bool(provider in SUPPORTED_PLATFORMS and oauth_push_receive_id(account))


def select_latest_oauth_account(
    accounts: Iterable["UserOAuthAccount"],
) -> Optional["UserOAuthAccount"]:
    """在可推送账号中，按 update_time、create_time、uid 选取较新的一条。"""
    valid = [account for account in accounts if is_deliverable_oauth_account(account)]
    if not valid:
        return None
    return max(
        valid,
        key=lambda account: (
            _oauth_account_sort_datetime(getattr(account, "update_time", None)),
            _oauth_account_sort_datetime(getattr(account, "create_time", None)),
            str(getattr(account, "uid", "") or ""),
        ),
    )

