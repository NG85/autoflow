from typing import Optional
from uuid import UUID
from sqlalchemy import JSON
from sqlmodel import (
    Field,
    SQLModel,
    Relationship as SQLRelationship,
)
from app.models.user_oauth_account import UserOAuthAccount

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
    
    # # 飞书相关字段
    # feishu_open_id: Optional[str] = Field(max_length=255, nullable=True, description="飞书open_id，用于消息推送")
    
    # # 平台用户标识 - 统一使用open_id字段，通过platform区分平台
    # platform: Optional[str] = Field(max_length=50, nullable=True, description="平台名称 (feishu/lark/dingtalk/wecom etc.)")
    # open_id: Optional[str] = Field(max_length=255, nullable=True, description="平台用户标识 (open_id/user_id)")
    
    # 核心组织架构信息
    name: Optional[str] = Field(max_length=255, nullable=True, description="姓名")
    department: Optional[str] = Field(max_length=255, nullable=True, description="所属部门")
    position: Optional[str] = Field(max_length=255, nullable=True, description="职位/岗位")
    
    # 直属上级信息
    direct_manager_id: Optional[str] = Field(max_length=255, nullable=True, description="直属上级ID")
    direct_manager_name: Optional[str] = Field(max_length=255, nullable=True, description="直属上级姓名")
    
    # 状态信息
    is_active: bool = Field(default=True, description="档案是否有效")
    
    # 推送权限标签 - 字符串格式存储推送类型权限
    notification_tags: Optional[str] = Field(
        max_length=1000, 
        nullable=True, 
        description="推送权限标签，逗号分隔的字符串，如：review1,review5,weekly_report,daily_report,visit_record"
    )
    
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
    
    oauth_user: Optional["UserOAuthAccount"] = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "foreign(UserProfile.user_id) == UserOAuthAccount.user_id",
        },
    )
    __tablename__ = "user_profiles"
    
    class Config:
        orm_mode = True
    
    def get_platform_open_id(self, platform: str) -> Optional[str]:
        """
        获取用户在指定平台的open_id
        
        Args:
            platform: 平台名称 (feishu/lark/dingtalk/wecom etc.)
            
        Returns:
            对应的open_id，如果不存在返回None
        """
        if self.oauth_user and self.oauth_user.provider == platform:
            return self.oauth_user.open_id
        return None

    
    def has_platform_access(self, platform: str) -> bool:
        """
        检查用户是否在指定平台有访问权限
        
        Args:
            platform: 平台名称
            
        Returns:
            是否有该平台的open_id
        """
        return self.oauth_user and self.oauth_user.provider == platform and self.oauth_user.open_id is not None
    
    def get_available_platforms(self) -> list[str]:
        """
        获取用户可用的平台列表
        
        Returns:
            用户有open_id的平台列表
        """
        if self.oauth_user and self.oauth_user.provider:
            return [self.oauth_user.provider]
        return []
    
    def get_current_platform(self) -> Optional[str]:
        """
        获取用户当前的平台
        
        Returns:
            当前平台名称
        """
        return self.oauth_user.provider if self.oauth_user else None
        
    def get_notification_tags(self) -> list[str]:
        """
        获取用户的推送权限标签列表
        
        Returns:
            推送权限标签列表
        """
        if not self.notification_tags:
            return []
        
        # 使用逗号分隔的字符串格式
        return [tag.strip() for tag in self.notification_tags.split(',') if tag.strip()]
    
    def has_notification_permission(self, notification_type: str) -> bool:
        """
        检查用户是否有指定类型的推送权限
        
        Args:
            notification_type: 推送类型
            
        Returns:
            是否有该类型的推送权限
        """
        tags = self.get_notification_tags()
        return notification_type in tags
