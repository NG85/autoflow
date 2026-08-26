from typing import Optional, List, TYPE_CHECKING
from uuid import UUID
from sqlalchemy import JSON
from sqlmodel import (
    Field,
    SQLModel,
    Relationship as SQLRelationship,
)
from app.models.user_oauth_account import UserOAuthAccount, select_latest_oauth_account

if TYPE_CHECKING:
    from app.models.auth import User

class UserProfile(SQLModel, table=True):
    """
    用户档案表 - 存储用户的核心组织架构信息
    
    注意：此表在其他系统维护，本系统只能读取，不能进行写操作（INSERT/UPDATE/DELETE）
    """
    
    # 主键（只读，由其他系统维护）
    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID，由其他系统维护")
        
    # 关联字段 - 支持多种用户来源
    user_id: Optional[UUID] = Field(foreign_key="users.id", nullable=True, index=True, description="关联系统用户表")
    oauth_user_id: Optional[str] = Field(max_length=255, nullable=True, description="关联OAuth用户表的ask_id")
    
    
    # 核心组织架构信息
    name: Optional[str] = Field(max_length=255, nullable=True, description="姓名")
    department: Optional[str] = Field(max_length=255, nullable=True, description="所属部门")
    position: Optional[str] = Field(max_length=255, nullable=True, description="职位/岗位")
    
    # 直属上级信息
    direct_manager_id: Optional[str] = Field(max_length=255, nullable=True, description="直属上级ID")
    direct_manager_name: Optional[str] = Field(max_length=255, nullable=True, description="直属上级姓名")
    
    # 状态信息
    is_active: bool = Field(default=True, description="档案是否有效")
    
    # 新档案字段：更完整的个人信息（按“先兼容”策略从用户表user中迁移过来）
    en_name: Optional[str] = Field(
        max_length=100,
        nullable=True,
        description="用户英文名（新档案字段）",
    )
    avatar_url: Optional[str] = Field(
        max_length=255,
        nullable=True,
        description="头像URL（新档案字段）",
    )
    email: Optional[str] = Field(
        max_length=255,
        nullable=True,
        description="邮箱（新档案字段）",
    )
    phone: Optional[str] = Field(
        max_length=50,
        nullable=True,
        description="电话/手机号（新档案字段）",
    )
    crm_user_id: Optional[str] = Field(
        max_length=100,
        nullable=True,
        description="CRM 系统中的账号 ID（新档案字段）",
    )
    role: Optional[str] = Field(
        max_length=100,
        nullable=True,
        description="用户角色（新档案字段）",
    )
    extra: Optional[dict] = Field(
        default=None,
        sa_type=JSON(none_as_null=True),
        description="通用扩展字段（JSON 格式，按业务自定义）",
    )
    
    # 关联关系
    user: Optional["User"] = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "UserProfile.user_id == User.id",
        },
    )
    
    # 一对多关系：一个用户可能有多个OAuth账号（不同平台）
    # 注意：虽然设计上支持多账号，但实际业务场景中通常是 1 对 1（客户公司通常只使用一类 OAuth）
    oauth_users: List["UserOAuthAccount"] = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "selectin",  # 使用selectin加载，支持多账号场景
            "primaryjoin": "UserProfile.user_id == foreign(UserOAuthAccount.user_id)",
            "back_populates": None,  # UserOAuthAccount 不需要反向关系
        },
    )
    __tablename__ = "user_profiles"
    
    model_config = {"from_attributes": True}
    
    @property
    def oauth_user(self) -> Optional["UserOAuthAccount"]:
        """
        获取用户的OAuth账号
        
        在实际业务场景中，通常是 1 对 1 关系（客户公司通常只使用一类 OAuth）。
        若同一用户绑定多个平台，只考虑受支持且有 open_id 的账号，
        再按 update_time（其次 create_time）取较新的一条。与拜访推送同一套选择逻辑。
        
        Returns:
            可用于推送的 OAuth 账号；多绑定时取较新的一条，如果不存在返回None
        """
        return select_latest_oauth_account(self.oauth_users or [])
    
    def get_oauth_account_by_platform(self, platform: str) -> Optional["UserOAuthAccount"]:
        """
        根据平台获取对应的OAuth账号
        
        Args:
            platform: 平台名称 (feishu/lark/dingtalk/wecom etc.)
            
        Returns:
            对应平台的OAuth账号，如果不存在返回None
        """
        if not self.oauth_users:
            return None
        for oauth_account in self.oauth_users:
            if oauth_account.provider == platform:
                return oauth_account
        return None
    
    def get_platform_open_id(self, platform: str) -> Optional[str]:
        """
        获取用户在指定平台的open_id
        
        Args:
            platform: 平台名称 (feishu/lark/dingtalk/wecom etc.)
            
        Returns:
            对应的open_id，如果不存在返回None
        """
        oauth_account = self.get_oauth_account_by_platform(platform)
        if oauth_account:
            return oauth_account.open_id
        return None

    
    def has_platform_access(self, platform: str) -> bool:
        """
        检查用户是否在指定平台有访问权限
        
        Args:
            platform: 平台名称
            
        Returns:
            是否有该平台的open_id
        """
        oauth_account = self.get_oauth_account_by_platform(platform)
        return bool(oauth_account and oauth_account.open_id is not None)
    
    def get_available_platforms(self) -> list[str]:
        """
        获取用户可用的平台列表
        
        在实际业务场景中（1 对 1 关系），通常只返回一个平台。
        此方法保留多账号场景的支持。
        
        Returns:
            用户有open_id的平台列表（在 1 对 1 场景下通常只有一个元素）
        """
        if not self.oauth_users:
            return []
        platforms = []
        for oauth_account in self.oauth_users:
            if oauth_account.provider and oauth_account.open_id:
                platforms.append(oauth_account.provider)
        return platforms
    
    def get_current_platform(self) -> Optional[str]:
        """
        获取用户当前的平台
        
        与 oauth_user 相同：多绑定时返回较新的可推送账号所在平台。
        
        Returns:
            当前平台名称，如果不存在返回None
        """
        if self.oauth_user and self.oauth_user.provider:
            return self.oauth_user.provider
        return None
