from typing import Optional
from uuid import UUID
from fastapi_pagination import Page, Params
from fastapi_pagination.ext.sqlmodel import paginate
from sqlmodel import Session, select, or_
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

    def get_user_profile_for_feishu(self, db_session: Session, user_id: UUID) -> Optional[dict]:
        """获取用户档案信息用于飞书消息推送"""
        query = (
            select(User, UserProfile, OAuthUser)
            .outerjoin(UserProfile, User.id == UserProfile.user_id)
            .outerjoin(
                OAuthUser,
                OAuthUser.ask_id == UserProfile.oauth_user_id,
            )
            .where(User.id == user_id)
        )
        result = db_session.exec(query).first()
        
        if result:
            user, profile, oauth_user = result
            return {
                "user_id": user.id,
                "email": user.email,
                "feishu_open_id": (
                    profile.feishu_open_id if profile and profile.feishu_open_id else (
                        oauth_user.open_id if oauth_user else None
                    )
                ),
                "name": profile.name if profile else (oauth_user.name if oauth_user else None),
                "department": profile.department if profile else None,
                "position": profile.position if profile else None,
                "direct_manager_id": profile.direct_manager_id if profile else None,
                "direct_manager_name": profile.direct_manager_name if profile else None,
            }
        return None

    def get_users_by_department(self, db_session: Session, department: str) -> list[dict]:
        """根据部门获取所有用户档案信息（用于飞书推送）"""
        query = (
            select(User, UserProfile, OAuthUser)
            .join(UserProfile, User.id == UserProfile.user_id)
            .outerjoin(
                OAuthUser,
                OAuthUser.ask_id == UserProfile.oauth_user_id,
            )
            .where(UserProfile.department == department)
            .where(UserProfile.is_active == True)
        )
        results = db_session.exec(query).all()
        
        return [
            {
                "user_id": user.id,
                "email": user.email,
                "feishu_open_id": (profile.feishu_open_id if profile.feishu_open_id else (oauth_user.open_id if oauth_user else None)),
                "name": profile.name if profile.name else (oauth_user.name if oauth_user else None),
                "department": profile.department,
                "position": profile.position,
                "direct_manager_id": profile.direct_manager_id,
                "direct_manager_name": profile.direct_manager_name,
            }
            for user, profile, oauth_user in results
        ]

    def get_manager_and_subordinates(self, db_session: Session, manager_id: str) -> dict:
        """获取管理者及其下属信息（用于飞书推送）"""
        # 获取管理者信息
        manager_query = (
            select(User, UserProfile, OAuthUser)
            .join(UserProfile, User.id == UserProfile.user_id)
            .outerjoin(
                OAuthUser,
                OAuthUser.ask_id == UserProfile.oauth_user_id,
            )
            .where(UserProfile.direct_manager_id == manager_id)
            .where(UserProfile.is_active == True)
        )
        manager_result = db_session.exec(manager_query).first()
        
        # 获取下属信息
        subordinates_query = (
            select(User, UserProfile, OAuthUser)
            .join(UserProfile, User.id == UserProfile.user_id)
            .outerjoin(
                OAuthUser,
                OAuthUser.ask_id == UserProfile.oauth_user_id,
            )
            .where(UserProfile.direct_manager_id == manager_id)
            .where(UserProfile.is_active == True)
        )
        subordinates_results = db_session.exec(subordinates_query).all()
        
        manager_info = None
        if manager_result:
            user, profile, oauth_user = manager_result
            manager_info = {
                "user_id": user.id,
                "email": user.email,
                "feishu_open_id": (profile.feishu_open_id if profile.feishu_open_id else (oauth_user.open_id if oauth_user else None)),
                "name": profile.name if profile.name else (oauth_user.name if oauth_user else None),
                "department": profile.department,
                "position": profile.position,
                "direct_manager_id": profile.direct_manager_id,
                "direct_manager_name": profile.direct_manager_name,
            }
        
        subordinates = [
            {
                "user_id": user.id,
                "email": user.email,
                "feishu_open_id": (profile.feishu_open_id if profile.feishu_open_id else (oauth_user.open_id if oauth_user else None)),
                "name": profile.name if profile.name else (oauth_user.name if oauth_user else None),
                "department": profile.department,
                "position": profile.position,
                "direct_manager_id": profile.direct_manager_id,
                "direct_manager_name": profile.direct_manager_name,
            }
            for user, profile, oauth_user in subordinates_results
        ]
        
        return {
            "manager": manager_info,
            "subordinates": subordinates
        }

    def search_profiles(
        self,
        db_session: Session,
        search: Optional[str] = None,
        department: Optional[str] = None,
        is_active: Optional[bool] = None,
        params: Params = Params(),
    ) -> Page[UserProfile]:
        """搜索用户档案"""
        query = select(UserProfile)

        # 添加搜索条件
        if search:
            search_condition = or_(
                UserProfile.department.ilike(f"%{search}%"),
                UserProfile.position.ilike(f"%{search}%"),
                UserProfile.direct_manager_name.ilike(f"%{search}%"),
            )
            query = query.where(search_condition)

        # 添加过滤条件
        if department:
            query = query.where(UserProfile.department == department)
        
        if is_active is not None:
            query = query.where(UserProfile.is_active == is_active)

        query = query.order_by(UserProfile.id)
        return paginate(db_session, query, params)

    def get_department_members(
        self, 
        db_session: Session, 
        department: str
    ) -> list[UserProfile]:
        """获取部门成员"""
        query = select(UserProfile).where(UserProfile.department == department)
        return db_session.exec(query).all()

    def get_subordinates(
        self, 
        db_session: Session, 
        manager_id: str
    ) -> list[UserProfile]:
        """获取下属"""
        query = select(UserProfile).where(UserProfile.direct_manager_id == manager_id)
        return db_session.exec(query).all()

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

    def get_department_managers(
        self, 
        db_session: Session, 
        department: str
    ) -> list[UserProfile]:
        """获取部门所有负责人（部门中没有直属上级的用户）"""
        return db_session.exec(
            select(UserProfile).where(
                UserProfile.department == department,
                UserProfile.direct_manager_id.is_(None),  # 没有直属上级
                UserProfile.is_active == True
            )
        ).all()
