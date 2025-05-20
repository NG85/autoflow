from functools import lru_cache
from typing import Optional
from uuid import UUID
from app.repositories.base_repo import BaseRepo
from sqlmodel import select, Session, or_, and_, join
from app.models.file_permission import FilePermission, PermissionType
from app.models.upload import Upload
from datetime import datetime, UTC
from app.models.document import DocumentCategory
import logging

logger = logging.getLogger(__name__)

class FilePermissionRepo(BaseRepo):
    model_cls = FilePermission
    
    def _get_active_condition(self):
        """获取权限有效的条件：未过期或永久有效"""
        return or_(
            FilePermission.expires_at == None,  # 永久有效
            FilePermission.expires_at > datetime.now(UTC)  # 未过期
        )
    
    def _get_category_condition(self, category: DocumentCategory, operator):
        """使用虚拟列category进行匹配
        
        Args:
            category: 文档分类
            operator: 比较操作符，支持 'eq'(相等) 和 'ne'(不相等)
        """
        if not category:
            return None
        
        if operator == "eq":
            return Upload.category == category.value
        elif operator == "ne":
            return Upload.category != category.value
        else:
            raise ValueError(f"Unsupported operator: {operator}")
    
    @lru_cache(maxsize=50)
    def get_user_accessible_file_ids(self, session: Session, user_id: UUID, category: Optional[DocumentCategory] = None, operator: Optional[str] = None) -> list[int]:
        """获取用户有权限访问的所有文件ID
        
        逻辑：
        1. 默认所有文件都是公开的，不需要在 file_permissions 表中记录
        2. 如果文件在 file_permissions 表中有记录，说明该文件有特殊权限设置
        3. 用户有权限访问的文件包括：
           - 所有不在 file_permissions 表中的文件（默认公开）
           - 在 file_permissions 表中且用户有权限的文件
        """
        try:
            # 构建基础查询
            base_conditions = []
            if category:
                base_conditions.append(self._get_category_condition(category, operator))
    
            # 检查该用户在权限表中是否有任何记录
            has_permissions = session.exec(
                select(FilePermission.id).where(
                    FilePermission.user_id == user_id
                ).limit(1)
            ).first() is not None

            if not has_permissions:
                # # 如果该用户在权限表中没有任何记录，返回所有文件ID
                # return session.exec(
                #     select(Upload.id).where(*base_conditions)
                # ).all()
                # 如果该用户在权限表中没有任何记录，返回空列表 - 代表所有文件权限都公开
                return []

            # 1. 获取没有权限记录的文件（默认公开）
            public_files = select(Upload.id).where(
                and_(
                    ~Upload.id.in_(select(FilePermission.file_id)),
                    *base_conditions
                )
            )
            
            # 2. 获取用户有权限的文件
            user_files = select(FilePermission.file_id).where(
                and_(
                    FilePermission.user_id == user_id,
                    self._get_active_condition(),
                    *base_conditions
                )
            )
            
            # 合并结果
            stmt = public_files.union(user_files)
            return [row[0] for row in session.exec(stmt).all()]
        except Exception as e:
            logger.error(f"Error getting user accessible file IDs: {e}")
            return []
    
    @lru_cache(maxsize=1000)
    def check_user_has_permission(self, session: Session, user_id: UUID, file_id: int) -> bool:
        """检查用户是否有权限访问特定文件
        
        逻辑：
        1. 文件在uploads表中存在
        1. 如果文件在 file_permissions 表中没有记录，则默认有权限（公开）
        2. 如果文件在 file_permissions 表中有记录，则按照记录中的权限处理
        """
        try:
            # 首先检查文件是否存在
            file_exists = session.exec(select(Upload.id).where(
                and_(
                    Upload.id == file_id
                )
            )).first() is not None
            
            if not file_exists:
                return False
        
            # 查询该文件是否有权限记录
            permission = session.exec(
                select(FilePermission).where(
                    and_(
                        FilePermission.file_id == file_id,
                        self._get_active_condition()
                    )
                )
            ).first()
            
            if permission is None:
                # 没有权限记录，默认公开
                return True
            
            # 有权限记录，检查用户权限
            return permission.user_id == user_id
        except Exception as e:
            logger.error(f"Error checking user permission for file {file_id}: {e}")
            return False
    
    def get_public_file_ids(self, session: Session, category: Optional[DocumentCategory] = None, operator: Optional[str] = None) -> list[int]:
        """获取所有公开文件的ID
        
        逻辑：
        1. 默认所有文件都是公开的，不需要在 file_permissions 表中记录
        2. 如果文件在 file_permissions 表中有记录，则按照记录中的权限处理
        """
        try:
            # 构建基础查询
            base_conditions = []
            if category:
                base_conditions.append(self._get_category_condition(category, operator))
            
            # 获取没有权限记录的文件（默认公开）
            stmt = select(Upload.id).where(
                and_(
                    ~Upload.id.in_(select(FilePermission.file_id)),
                    *base_conditions
                )
            )
            
            return session.exec(stmt).all()
        except Exception as e:
            logger.error(f"Error getting public file IDs: {e}")
            return []
    
    def get_user_personal_file_ids(self, session: Session, user_id: UUID) -> list[int]:
        """获取用户有个人权限的文件ID列表"""
        # 构建基础查询
        base_conditions = [
            FilePermission.user_id == user_id,
            self._get_active_condition()
        ]
        
        stmt = select(FilePermission.file_id).where(and_(*base_conditions))
        return session.exec(stmt).all()
    
    def check_user_is_owner(self, session: Session, user_id: UUID, file_id: int) -> bool:
        """检查用户是否是文件的所有者"""
        try:
            with session.begin():
                # 构建基础查询
                base_conditions = [
                    FilePermission.file_id == file_id,
                    FilePermission.user_id == user_id,
                    FilePermission.permission_type == PermissionType.OWNER,
                    self._get_active_condition()
                ]
                
                stmt = select(FilePermission.file_id).where(and_(*base_conditions))
                return session.exec(stmt).first() is not None
        except Exception as e:
            logger.error(f"Error checking user is owner for file {file_id}: {e}")
            return False

file_permission_repo = FilePermissionRepo()