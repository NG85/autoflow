from typing import Optional
from uuid import UUID
from sqlmodel import Field, SQLModel
from sqlalchemy import UniqueConstraint


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
        
    def __repr__(self):
        return f"<UserOAuthAccount(uid={self.uid}, user_id={str(self.user_id)}, provider='{self.provider}', open_id='{self.open_id}', union_id='{self.union_id}')>"

