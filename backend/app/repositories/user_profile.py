from typing import Optional, List, Dict
from uuid import UUID
from sqlmodel import Session, select, distinct
from sqlalchemy.orm import selectinload
from app.models.user_profile import UserProfile
from app.models.user_oauth_account import UserOAuthAccount
from app.repositories.base_repo import BaseRepo


class UserProfileRepo(BaseRepo):
    model_cls = UserProfile

    def get_by_user_id(self, db_session: Session, user_id: UUID) -> Optional[UserProfile]:
        """根据用户ID获取档案"""
        query = select(UserProfile).options(selectinload(UserProfile.oauth_users)).where(UserProfile.user_id == user_id)
        return db_session.exec(query).first()

    def get_crm_user_id_by_user_id(self, db_session: Session, user_id: UUID) -> Optional[str]:
        """根据系统 user_id(UUID) 获取 CRM user id（user_profiles.crm_user_id）"""
        row = db_session.exec(
            select(UserProfile.crm_user_id).where(
                UserProfile.user_id == user_id,
                UserProfile.is_active == True,  # noqa: E712
                UserProfile.crm_user_id.is_not(None),
            )
        ).first()
        return row if isinstance(row, str) and row else None

    def is_admin_by_user_id(self, db_session: Session, user_id: UUID) -> bool:
        """根据系统 user_id(UUID) 判断用户是否为管理员（role/position 任一为 admin）"""
        row = db_session.exec(
            select(UserProfile.role, UserProfile.position).where(
                UserProfile.user_id == user_id,
                UserProfile.is_active == True,  # noqa: E712
            )
        ).first()
        if not row:
            return False
        role, position = row
        return role == "admin" or position == "admin"

    def get_crm_user_id_and_role_by_user_id(
        self, db_session: Session, user_id: UUID
    ) -> tuple[Optional[str], Optional[str]]:
        """根据系统 user_id(UUID) 获取 (crm_user_id, role)。

        规则：
        - 如果 role/position 任一为 admin，则 role 归一化为 "admin"
        - 否则返回 user_profiles.role（可能为 None）
        """
        row = db_session.exec(
            select(UserProfile.crm_user_id, UserProfile.role, UserProfile.position).where(
                UserProfile.user_id == user_id,
                UserProfile.is_active == True,  # noqa: E712
            )
        ).first()
        if not row:
            return None, None
        crm_user_id, role, position = row
        normalized_role = "admin" if (role == "admin" or position == "admin") else role
        return (str(crm_user_id) if crm_user_id else None), (str(normalized_role) if normalized_role else None)

    def get_by_oauth_user_id(self, db_session: Session, oauth_user_id: str) -> Optional[UserProfile]:
        """根据OAuth用户ID获取档案"""
        query = select(UserProfile).options(selectinload(UserProfile.oauth_users)).where(UserProfile.oauth_user_id == oauth_user_id)
        return db_session.exec(query).first()

    def get_crm_user_id_by_oauth_user_id(self, db_session: Session, oauth_user_id: str) -> Optional[str]:
        """根据 oauth_user_id 获取 CRM user id（user_profiles.crm_user_id）"""
        row = db_session.exec(
            select(UserProfile.crm_user_id).where(
                UserProfile.oauth_user_id == oauth_user_id,
                UserProfile.is_active == True,  # noqa: E712
                UserProfile.crm_user_id.is_not(None),
            )
        ).first()
        return row if isinstance(row, str) and row else None


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
                select(UserProfile)
                .options(selectinload(UserProfile.oauth_users))
                .where(
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
                    select(UserProfile)
                    .options(selectinload(UserProfile.oauth_users))
                    .where(
                        UserProfile.name == name,
                        UserProfile.department == department,
                        UserProfile.is_active == True
                    )
                ).all()
            else:
                # 如果部门为空，只按姓名查找
                profiles = db_session.exec(
                    select(UserProfile)
                    .options(selectinload(UserProfile.oauth_users))
                    .where(
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
            select(UserProfile).options(selectinload(UserProfile.oauth_users)).where(UserProfile.is_active == True)
        ).all()


    def get_department_members(
        self, 
        db_session: Session, 
        department: str
    ) -> list[UserProfile]:
        """获取部门成员"""
        query = select(UserProfile).options(selectinload(UserProfile.oauth_users)).where(UserProfile.department == department)
        return db_session.exec(query).all()

    def get_oauth_user_ids_by_departments(self, db_session: Session, departments: List[str]) -> List[str]:
        """按部门列表批量获取 oauth_user_id（用于 visit record 等场景的过滤）"""
        depts = [d for d in (departments or []) if d]
        if not depts:
            return []
        rows = db_session.exec(
            select(UserProfile.oauth_user_id).where(
                UserProfile.department.in_(depts),
                UserProfile.oauth_user_id.is_not(None),
                UserProfile.is_active == True,
            )
        ).all()
        # rows 可能是 str 或 tuple；统一为 str 列表
        return [r for r in rows if isinstance(r, str) and r]

    def get_department_by_oauth_user_ids(self, db_session: Session, oauth_user_ids: List[str]) -> Dict[str, Optional[str]]:
        """
        批量获取用户部门映射（按 oauth_user_id 分组）。

        Returns:
            Dict[str, Optional[str]]: oauth_user_id -> department
        """
        ids = [x for x in (oauth_user_ids or []) if x]
        if not ids:
            return {}
        rows = db_session.exec(
            select(UserProfile.oauth_user_id, UserProfile.department).where(
                UserProfile.oauth_user_id.in_(ids),
                UserProfile.is_active == True,
            )
        ).all()
        result: Dict[str, Optional[str]] = {}
        for oid, dept in rows:
            if not oid:
                continue
            result[str(oid)] = dept
        return result

    def get_subordinates(self, db_session: Session, manager_id: str) -> List[UserProfile]:
        """获取指定管理者的直接下属"""
        return db_session.exec(
            select(UserProfile)
            .options(selectinload(UserProfile.oauth_users))
            .where(
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
            select(UserProfile)
            .options(selectinload(UserProfile.oauth_users))
            .where(
                UserProfile.department == department,
                UserProfile.direct_manager_id.is_(None),  # 没有直属上级
                UserProfile.is_active == True
            )
        ).first()

    def get_all_departments_with_managers(
        self, 
        db_session: Session
    ) -> Dict[str, Optional[UserProfile]]:
        """获取所有部门及其负责人
        
        Args:
            db_session: 数据库会话
            
        Returns:
            Dict[str, Optional[UserProfile]]: 字典，键是部门名称，值是部门负责人（如果没有负责人则为None）
        """
        # 获取所有不重复的部门名称（只考虑活跃用户）
        departments = db_session.exec(
            select(distinct(UserProfile.department))
            .where(
                UserProfile.department.is_not(None),
                UserProfile.is_active == True
            )
            .order_by(UserProfile.department)
        ).all()
        
        # 为每个部门查找负责人
        result = {}
        for department_name in departments:
            manager = self.get_department_manager(db_session, department_name)
            result[department_name] = manager
        
        return result

    def get_users_by_notification_permission(
        self, 
        db_session: Session, 
        notification_type: str
    ) -> list[UserProfile]:
        """根据推送权限类型获取用户列表
        
        在应用层精确匹配，避免子字符串匹配问题
        例如：notification_type='visit_record' 不会匹配到 'list_visit_records'
        """
        # 使用 join 查询所有有 open_id 和 notification_tags 的活跃用户
        # 由于一个用户可能有多个OAuth账号，使用 distinct 去重
        candidates = db_session.exec(
            select(UserProfile)
            .options(selectinload(UserProfile.oauth_users))
            .join(UserOAuthAccount, UserProfile.user_id == UserOAuthAccount.user_id)
            .where(
                UserProfile.is_active == True,
                UserOAuthAccount.open_id.is_not(None),
                UserProfile.notification_tags.is_not(None)
            )
            .distinct()
        ).all()
        
        # 在应用层使用模型的 has_notification_permission 方法精确匹配
        return [user for user in candidates if user.has_notification_permission(notification_type)]

user_profile_repo = UserProfileRepo()