from typing import List, Optional
from uuid import UUID
from sqlmodel import Session, select
from app.models.customer_document import CustomerDocument
from app.models.auth import User
from app.repositories.base_repo import BaseRepo
from datetime import datetime


class CustomerDocumentRepo(BaseRepo):
    """客户文档仓库"""
    
    def create_customer_document(
        self,
        session: Session,
        file_category: str,
        account_name: str,
        account_id: str,
        document_url: str,
        uploader_id: UUID,
        uploader_name: str,
        document_type: Optional[str] = None,
        document_title: Optional[str] = None,
        document_content_id: Optional[int] = None,
        auto_commit: bool = False
    ) -> CustomerDocument:
        """创建客户文档记录"""
        customer_document = CustomerDocument(
            file_category=file_category,
            account_name=account_name,
            account_id=account_id,
            document_url=document_url,
            document_type=document_type,
            document_title=document_title,
            uploader_id=uploader_id,
            uploader_name=uploader_name,
            document_content_id=document_content_id
        )
        
        session.add(customer_document)
        
        if auto_commit:
            session.commit()
            session.refresh(customer_document)
        
        return customer_document
    
    def get_customer_document_by_id(
        self,
        session: Session,
        document_id: int
    ) -> Optional[CustomerDocument]:
        """根据ID获取客户文档"""
        statement = select(CustomerDocument).where(CustomerDocument.id == document_id)
        return session.exec(statement).first()
    
    def get_customer_document_by_id_with_user(
        self,
        session: Session,
        document_id: int
    ) -> Optional[CustomerDocument]:
        """根据ID获取客户文档（包含用户信息）"""
        statement = select(CustomerDocument, User).join(
            User, CustomerDocument.uploader_id == User.id
        ).where(CustomerDocument.id == document_id)
        result = session.exec(statement).first()
        if result:
            document, user = result
            document.uploader = user
            return document
        return None
    
    def get_customer_documents_by_customer(
        self,
        session: Session,
        account_id: str,
        file_category: Optional[str] = None
    ) -> List[CustomerDocument]:
        """根据客户ID获取文档列表"""
        statement = select(CustomerDocument).where(CustomerDocument.account_id == account_id)
        
        if file_category:
            statement = statement.where(CustomerDocument.file_category == file_category)
        
        statement = statement.order_by(CustomerDocument.created_at.desc())
        
        return session.exec(statement).all()
    
    def get_customer_documents_by_customer_with_user(
        self,
        session: Session,
        account_id: str,
        file_category: Optional[str] = None
    ) -> List[CustomerDocument]:
        """根据客户ID获取文档列表（包含用户信息）"""
        statement = select(CustomerDocument, User).join(
            User, CustomerDocument.uploader_id == User.id
        ).where(CustomerDocument.account_id == account_id)
        
        if file_category:
            statement = statement.where(CustomerDocument.file_category == file_category)
        
        statement = statement.order_by(CustomerDocument.created_at.desc())
        
        results = session.exec(statement).all()
        documents = []
        for document, user in results:
            document.uploader = user
            documents.append(document)
        return documents
    
    def get_customer_documents_by_category(
        self,
        session: Session,
        file_category: str
    ) -> List[CustomerDocument]:
        """根据文件类别获取文档列表"""
        statement = select(CustomerDocument).where(
            CustomerDocument.file_category == file_category
        ).order_by(CustomerDocument.created_at.desc())
        
        return session.exec(statement).all()
    
    def get_customer_documents_by_category_with_user(
        self,
        session: Session,
        file_category: str
    ) -> List[CustomerDocument]:
        """根据文件类别获取文档列表（包含用户信息）"""
        statement = select(CustomerDocument, User).join(
            User, CustomerDocument.uploader_id == User.id
        ).where(CustomerDocument.file_category == file_category).order_by(CustomerDocument.created_at.desc())
        
        results = session.exec(statement).all()
        documents = []
        for document, user in results:
            document.uploader = user
            documents.append(document)
        return documents
    
    def get_customer_documents_by_uploader(
        self,
        session: Session,
        uploader_id: UUID,
        file_category: Optional[str] = None
    ) -> List[CustomerDocument]:
        """根据上传者ID获取文档列表"""
        statement = select(CustomerDocument).where(CustomerDocument.uploader_id == uploader_id)
        
        if file_category:
            statement = statement.where(CustomerDocument.file_category == file_category)
        
        statement = statement.order_by(CustomerDocument.created_at.desc())
        
        return session.exec(statement).all()
    
    def update_document_content_id(
        self,
        session: Session,
        document_id: int,
        document_content_id: int
    ) -> Optional[CustomerDocument]:
        """更新文档内容ID"""
        customer_document = self.get_customer_document_by_id(session, document_id)
        if customer_document:
            customer_document.document_content_id = document_content_id
            customer_document.updated_at = datetime.now()
            session.commit()
            session.refresh(customer_document)
        
        return customer_document
