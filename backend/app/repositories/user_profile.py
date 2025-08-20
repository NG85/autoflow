from typing import Optional, List, Dict, Any
from uuid import UUID
from sqlmodel import Session, select
from app.models.user_profile import UserProfile
from app.models.auth import User
from app.models.oauth_user import OAuthUser
from app.repositories.base_repo import BaseRepo


class UserProfileRepo(BaseRepo):
    model_cls = UserProfile

    def get_by_user_id(self, db_session: Session, user_id: UUID) -> Optional[UserProfile]:
        """根据用户ID获取档案"""
        query = select(UserProfile).where(UserProfile.user_id == user_id)
        return db_session.exec(query).first()

    def get_by_oauth_user_id(self, db_session: Session, oauth_user_id: str) -> Optional[UserProfile]:
        """根据OAuth用户ID获取档案"""
        query = select(UserProfile).where(UserProfile.oauth_user_id == oauth_user_id)
        return db_session.exec(query).first()

    def get_by_recorder_id(self, db_session: Session, recorder_id: str) -> Optional[UserProfile]:
        """根据记录人ID获取档案"""
        # 尝试作为OAuth用户ID查找
        profile = self.get_by_oauth_user_id(db_session, recorder_id)
        if profile:
            return profile
        
        # 如果不是OAuth ID，尝试作为系统用户ID查找
        try:
            from uuid import UUID
            user_uuid = UUID(recorder_id)
            return self.get_by_user_id(db_session, user_uuid)
        except ValueError:
            # 如果不是有效的UUID，返回None
            return None

    def get_by_name(self, db_session: Session, name: str) -> Optional[UserProfile]:
        """通过姓名查找用户档案（精确匹配）"""
        if not name or not name.strip():
            return None
            
        name = name.strip()
        
        try:
            profiles = db_session.exec(
                select(UserProfile).where(
                    UserProfile.name == name,
                    UserProfile.is_active == True
                )
            ).all()
            
            if profiles:
                return profiles[0]  # 返回第一个匹配的
            return None
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in get_by_name for '{name}': {e}")
            return None
    
    def get_by_name_and_department(self, db_session: Session, name: str, department: str) -> Optional[UserProfile]:
        """通过姓名和部门组合查找用户档案（精确匹配）"""
        if not name or not name.strip():
            return None
            
        name = name.strip()
        department = department.strip() if department else None
        
        try:
            if department:
                # 使用姓名和部门组合查找
                profiles = db_session.exec(
                    select(UserProfile).where(
                        UserProfile.name == name,
                        UserProfile.department == department,
                        UserProfile.is_active == True
                    )
                ).all()
            else:
                # 如果部门为空，只按姓名查找
                profiles = db_session.exec(
                    select(UserProfile).where(
                        UserProfile.name == name,
                        UserProfile.is_active == True
                    )
                ).all()
            
            if profiles:
                return profiles[0]  # 返回第一个匹配的
            return None
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in get_by_name_and_department for '{name}' in '{department}': {e}")
            return None

    def get_all_active_profiles(self, db_session: Session) -> List[UserProfile]:
        """获取所有有效的用户档案"""
        return db_session.exec(
            select(UserProfile).where(UserProfile.is_active == True)
        ).all()


    def get_department_members(
        self, 
        db_session: Session, 
        department: str
    ) -> list[UserProfile]:
        """获取部门成员"""
        query = select(UserProfile).where(UserProfile.department == department)
        return db_session.exec(query).all()

    def get_subordinates(self, db_session: Session, manager_id: str) -> List[UserProfile]:
        """获取指定管理者的直接下属"""
        return db_session.exec(
            select(UserProfile).where(
                UserProfile.direct_manager_id == manager_id,
                UserProfile.is_active == True
            )
        ).all()

    def get_all_subordinates_recursive(self, db_session: Session, manager_id: str) -> List[UserProfile]:
        """
        递归获取指定管理者的所有汇报关系（包括直接下属和间接下属）
        """
        all_subordinates = []
        
        # 获取直接下属
        direct_subordinates = self.get_subordinates(db_session, manager_id)
        
        for subordinate in direct_subordinates:
            # 添加直接下属
            all_subordinates.append(subordinate)
            
            # 递归获取间接下属
            if subordinate.oauth_user_id:
                indirect_subordinates = self.get_all_subordinates_recursive(db_session, subordinate.oauth_user_id)
                all_subordinates.extend(indirect_subordinates)
        
        return all_subordinates

    def get_department_manager(
        self, 
        db_session: Session, 
        department: str
    ) -> Optional[UserProfile]:
        """获取部门负责人（部门中没有直属上级的用户）"""
        return db_session.exec(
            select(UserProfile).where(
                UserProfile.department == department,
                UserProfile.direct_manager_id.is_(None),  # 没有直属上级
                UserProfile.is_active == True
            )
        ).first()

    def get_users_by_notification_permission(
        self, 
        db_session: Session, 
        notification_type: str
    ) -> list[UserProfile]:
        """根据推送权限类型获取用户列表"""
        query = select(UserProfile).where(
            UserProfile.is_active == True,
            UserProfile.open_id.is_not(None),
            UserProfile.notification_tags.contains(notification_type)
        )
        
        return db_session.exec(query).all()
