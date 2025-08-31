import logging
from typing import Dict, Any, Optional, List
from uuid import UUID
from sqlmodel import Session, select, or_
from app.models.customer_document import CustomerDocument
from app.repositories.customer_document import CustomerDocumentRepo
from app.repositories.document_content import DocumentContentRepo
from app.services.document_processing_service import DocumentProcessingService

logger = logging.getLogger(__name__)


class CustomerDocumentService:
    """客户文档服务"""
    
    def __init__(self):
        self.customer_document_repo = CustomerDocumentRepo()
        self.document_content_repo = DocumentContentRepo()
        self.document_processing_service = DocumentProcessingService()
    
    def upload_customer_document(
        self,
        db_session: Session,
        file_category: str,
        account_name: str,
        account_id: str,
        document_url: str,
        uploader_id: UUID,
        uploader_name: str,
        feishu_auth_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        上传客户文档
        
        Args:
            db_session: 数据库会话
            file_category: 文件类别
            account_name: 客户名称
            account_id: 客户ID
            document_url: 文档链接
            uploader_id: 上传者ID
            uploader_name: 上传者姓名
            feishu_auth_code: 飞书授权码
            
        Returns:
            上传结果
        """
        # 使用通用文档处理服务
        result = self.document_processing_service.process_document_url(
            document_url=document_url,
            user_id=str(uploader_id),
            feishu_auth_code=feishu_auth_code
        )
        
        # 如果处理失败，直接返回结果
        if not result.get("success"):
            return result
        
        # 处理成功，保存文档内容和客户文档记录
        return self._save_document_with_content(
            db_session=db_session,
            file_category=file_category,
            account_name=account_name,
            account_id=account_id,
            document_url=document_url,
            uploader_id=uploader_id,
            uploader_name=uploader_name,
            content=result["content"],
            document_type=result["document_type"],
            title=result.get("title")
        )
    
    def _save_document_with_content(
        self,
        db_session: Session,
        file_category: str,
        account_name: str,
        account_id: str,
        document_url: str,
        uploader_id: UUID,
        uploader_name: str,
        content: str,
        document_type: str,
        title: Optional[str] = None
    ) -> Dict[str, Any]:
        """保存文档内容和客户文档记录"""
        try:
            # 1. 保存文档内容到document_contents表（不自动提交）
            document_content = self.document_content_repo.create_document_content(
                session=db_session,
                raw_content=content,
                document_type=document_type,
                source_url=document_url,
                user_id=uploader_id,
                title=title or f"{account_name}_{file_category}",
                auto_commit=False
            )
            
            # 2. 使用flush()生成ID，但不提交事务
            db_session.flush()
            
            # 3. 保存客户文档记录（不自动提交）
            customer_document = self.customer_document_repo.create_customer_document(
                session=db_session,
                file_category=file_category,
                account_name=account_name,
                account_id=account_id,
                document_url=document_url,
                uploader_id=uploader_id,
                uploader_name=uploader_name,
                document_type=document_type,
                document_title=title or f"{account_name}_{file_category}",
                document_content_id=document_content.id,
                auto_commit=False
            )
            
            # 4. 统一提交事务
            db_session.commit()
            
            # 4. 刷新对象以获取数据库生成的ID
            db_session.refresh(document_content)
            db_session.refresh(customer_document)
            
            logger.info(f"成功保存客户文档: {customer_document.id}, 内容ID: {document_content.id}")
            
            return {
                "success": True,
                "message": "文档上传成功",
                "document_id": customer_document.id
            }
            
        except Exception as e:
            db_session.rollback()
            logger.error(f"保存文档内容失败: {str(e)}")
            raise Exception(f"保存文档失败: {str(e)}")
    
    def get_customer_documents(
        self,
        db_session: Session,
        account_id: Optional[str] = None,
        file_category: Optional[str] = None,
        uploader_id: Optional[UUID] = None
    ) -> List[CustomerDocument]:
        """获取客户文档列表"""
        if account_id:
            return self.customer_document_repo.get_customer_documents_by_customer(
                db_session, account_id, file_category
            )
        elif file_category:
            return self.customer_document_repo.get_customer_documents_by_category(
                db_session, file_category
            )
        elif uploader_id:
            return self.customer_document_repo.get_customer_documents_by_uploader(
                db_session, uploader_id, file_category
            )
        else:
            return []
    
    def get_customer_documents_with_user_info(
        self,
        db_session: Session,
        account_id: Optional[str] = None,
        file_category: Optional[str] = None,
        uploader_id: Optional[UUID] = None
    ) -> List[CustomerDocument]:
        """获取客户文档列表（包含用户信息）"""
        if account_id:
            return self.customer_document_repo.get_customer_documents_by_customer_with_user(
                db_session, account_id, file_category
            )
        elif file_category:
            return self.customer_document_repo.get_customer_documents_by_category_with_user(
                db_session, file_category
            )
        elif uploader_id:
            return self.customer_document_repo.get_customer_documents_by_uploader(
                db_session, uploader_id, file_category
            )
        else:
            return []
    
    def get_customer_document_by_id(
        self,
        db_session: Session,
        document_id: int
    ) -> Optional[CustomerDocument]:
        """根据ID获取客户文档"""
        return self.customer_document_repo.get_customer_document_by_id(db_session, document_id)
    
    def get_customer_document_by_id_with_user(
        self,
        db_session: Session,
        document_id: int
    ) -> Optional[CustomerDocument]:
        """根据ID获取客户文档（包含用户信息）"""
        return self.customer_document_repo.get_customer_document_by_id_with_user(db_session, document_id)

    def get_customer_documents_with_permissions(
        self,
        db_session: Session,
        current_user_id: UUID,
        current_user_is_superuser: bool,
        current_user_department: Optional[str] = None,
        account_id: Optional[str] = None,
        file_category: Optional[str] = None,
        uploader_id: Optional[UUID] = None
    ) -> List[CustomerDocument]:
        """
        根据用户权限获取客户文档列表
        
        Args:
            db_session: 数据库会话
            current_user_id: 当前用户ID
            current_user_is_superuser: 当前用户是否为超级管理员
            current_user_department: 当前用户所在部门
            account_id: 客户ID（可选）
            file_category: 文件类别（可选）
            uploader_id: 上传者ID（可选）
            
        Returns:
            根据权限过滤的文档列表
        """
        from app.repositories.user_profile import UserProfileRepo
        
        # 获取用户档案
        user_profile_repo = UserProfileRepo()
        user_profile = user_profile_repo.get_by_oauth_user_id(db_session, current_user_id)
        
        # 检查是否为超级管理员或管理员
        is_superuser_or_admin = self._is_superuser_or_admin(
            db_session=db_session,
            user_id=current_user_id,
            user_is_superuser=current_user_is_superuser,
            user_profile=user_profile
        )
        
        # 超级管理员或管理员可以查看所有文档
        if is_superuser_or_admin:
            return self.get_customer_documents(
                db_session=db_session,
                account_id=account_id,
                file_category=file_category,
                uploader_id=uploader_id
            )
        
        # 普通用户只能查看自己上传的文档
        if not current_user_department:
            return self.get_customer_documents(
                db_session=db_session,
                uploader_id=current_user_id,
                file_category=file_category
            )
        
        # 团队lead可以查看本团队的所有文档
        team_members = user_profile_repo.get_department_members(db_session, current_user_department)
        team_member_ids = [str(member.oauth_user_id) for member in team_members if member.oauth_user_id]
        
        if not team_member_ids:
            return []
        
        # 构建查询条件
        statement = select(CustomerDocument).where(
            or_(*[CustomerDocument.uploader_id == member_id for member_id in team_member_ids])
        )
        
        if account_id:
            statement = statement.where(CustomerDocument.account_id == account_id)
        if file_category:
            statement = statement.where(CustomerDocument.file_category == file_category)
        if uploader_id:
            statement = statement.where(CustomerDocument.uploader_id == uploader_id)
        
        statement = statement.order_by(CustomerDocument.created_at.desc())
        
        return db_session.exec(statement).all()
    
    def get_customer_documents_with_permissions_and_user_info(
        self,
        db_session: Session,
        current_user_id: UUID,
        current_user_is_superuser: bool,
        current_user_department: Optional[str] = None,
        account_id: Optional[str] = None,
        file_category: Optional[str] = None,
        uploader_id: Optional[UUID] = None
    ) -> List[CustomerDocument]:
        """
        根据用户权限获取客户文档列表
        
        Args:
            db_session: 数据库会话
            current_user_id: 当前用户ID
            current_user_is_superuser: 当前用户是否为超级管理员
            current_user_department: 当前用户所在部门
            account_id: 客户ID（可选）
            file_category: 文件类别（可选）
            uploader_id: 上传者ID（可选）
            
        Returns:
            根据权限过滤的文档列表
        """
        # 直接调用权限控制方法，不再需要用户信息
        return self.get_customer_documents_with_permissions(
            db_session=db_session,
            current_user_id=current_user_id,
            current_user_is_superuser=current_user_is_superuser,
            current_user_department=current_user_department,
            account_id=account_id,
            file_category=file_category,
            uploader_id=uploader_id
        )

    def _is_superuser_or_admin(
        self,
        db_session: Session,
        user_id: UUID,
        user_is_superuser: bool,
        user_profile=None
    ) -> bool:
        """
        检查用户是否为超级管理员或管理员
        
        包括：
        1. is_superuser（系统超管）
        2. user_profiles中position=admin的人
        
        Args:
            db_session: 数据库会话
            user_id: 用户ID
            user_is_superuser: 用户是否为系统超级管理员
            user_profile: 用户档案（可选，如果已获取则直接使用）
            
        Returns:
            是否为超级管理员或管理员
        """
        # 1. 检查系统超级管理员
        if user_is_superuser:
            return True
        
        # 2. 检查user_profiles中position=admin的人
        if not user_profile:
            from app.repositories.user_profile import UserProfileRepo
            user_profile_repo = UserProfileRepo()
            user_profile = user_profile_repo.get_by_oauth_user_id(db_session, user_id)
        
        if user_profile and user_profile.position == "admin":
            return True
        
        return False
