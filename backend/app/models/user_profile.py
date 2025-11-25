from typing import Optional
from uuid import UUID
from sqlmodel import (
    Field,
    SQLModel,
    Relationship as SQLRelationship,
)
class UserProfile(SQLModel, table=True):
    """
    用户档案表 - 存储用户的核心组织架构信息
    
    注意：此表在其他系统维护，本系统只能读取，不能进行写操作（INSERT/UPDATE/DELETE）
    """
    
    # 主键（只读，由其他系统维护）
    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID，由其他系统维护")
        
    # 关联字段 - 支持多种用户来源
    user_id: Optional[UUID] = Field(foreign_key="users.id", nullable=True, description="关联系统用户表")
    oauth_user_id: Optional[str] = Field(max_length=255, nullable=True, description="关联OAuth用户表的ask_id")
    
    # 飞书相关字段
    feishu_open_id: Optional[str] = Field(max_length=255, nullable=True, description="飞书open_id，用于消息推送")
    
    # 平台用户标识 - 统一使用open_id字段，通过platform区分平台
    platform: Optional[str] = Field(max_length=50, nullable=True, description="平台名称 (feishu/lark/dingtalk/wecom etc.)")
    open_id: Optional[str] = Field(max_length=255, nullable=True, description="平台用户标识 (open_id/user_id)")
    
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
    
    # 关联关系
    user: Optional["User"] = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "UserProfile.user_id == User.id",
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
        if self.platform == platform:
            return self.open_id
        return None

    
    def has_platform_access(self, platform: str) -> bool:
        """
        检查用户是否在指定平台有访问权限
        
        Args:
            platform: 平台名称
            
        Returns:
            是否有该平台的open_id
        """
        return self.platform == platform and self.open_id is not None
    
    def get_available_platforms(self) -> list[str]:
        """
        获取用户可用的平台列表
        
        Returns:
            用户有open_id的平台列表
        """
        if self.platform and self.open_id:
            return [self.platform]
        return []
    
    def get_current_platform(self) -> Optional[str]:
        """
        获取用户当前的平台
        
        Returns:
            当前平台名称
        """
        return self.platform
        
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
