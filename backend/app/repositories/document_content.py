from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlmodel import Session, select
from app.models.document_contents import DocumentContent
from app.repositories.base_repo import BaseRepo

import logging
logger = logging.getLogger(__name__)

class DocumentContentRepo(BaseRepo):
    """文档内容仓库"""
    
    def create_document_content(
        self,
        session: Session,
        raw_content: str,
        document_type: str,
        source_url: str,
        user_id: UUID,
        title: Optional[str] = None,
        visit_record_id: Optional[str] = None,
        file_size: Optional[int] = None,
        auto_commit: bool = False
    ) -> DocumentContent:
        """创建文档内容记录"""
        # 如果没有提供文件大小，自动计算
        if file_size is None:
            file_size = len(raw_content.encode('utf-8')) if raw_content else 0
        
        document_content = DocumentContent(
            user_id=user_id,
            visit_record_id=visit_record_id,
            document_type=document_type,
            source_url=source_url,
            raw_content=raw_content,
            title=title,
            file_size=file_size
        )
        
        session.add(document_content)
        
        # 总是flush以生成ID，但不提交事务
        session.flush()
        
        if auto_commit:
            session.commit()
            session.refresh(document_content)
        
        return document_content
    
    def update_meeting_summary(
        self,
        session: Session,
        document_content_id: int,
        meeting_summary: str,
        summary_status: str = "success",
        auto_commit: bool = False
    ) -> Optional[DocumentContent]:
        """更新文档内容的会议纪要总结"""
        try:
            document_content = session.get(DocumentContent, document_content_id)
            if not document_content:
                return None
            
            document_content.meeting_summary = meeting_summary
            document_content.summary_status = summary_status
            
            if auto_commit:
                session.commit()
                session.refresh(document_content)
            
            return document_content
        except Exception as e:
            logger.error(f"更新会议纪要总结失败: {e}")
            return None
    
    def update_qa_pairs(
        self,
        session: Session,
        document_content_id: int,
        qa_pairs: List[Dict[str, Any]],
        qa_status: str = "success",
        auto_commit: bool = False
    ) -> Optional[DocumentContent]:
        """更新文档内容的问答对抽取结果"""
        try:
            document_content = session.get(DocumentContent, document_content_id)
            if not document_content:
                return None
            
            document_content.qa_pairs = qa_pairs
            document_content.qa_extract_status = qa_status
            
            if auto_commit:
                session.commit()
                session.refresh(document_content)
            
            return document_content
        except Exception as e:
            logger.error(f"更新问答对抽取结果失败: {e}")
            return None
    
    def update_risk_info(
        self,
        session: Session,
        document_content_id: int,
        risk_info: str,
        risk_status: str = "success",
        auto_commit: bool = False
    ) -> Optional[DocumentContent]:
        """更新文档内容的风险信息提取结果"""
        try:
            document_content = session.get(DocumentContent, document_content_id)
            if not document_content:
                return None
            
            document_content.risk_info = risk_info
            document_content.risk_extract_status = risk_status
            
            if auto_commit:
                session.commit()
                session.refresh(document_content)
            
            return document_content
        except Exception as e:
            logger.error(f"更新风险信息提取结果失败: {e}")
            return None
    
    def get_by_visit_record_id(
        self,
        session: Session,
        visit_record_id: str
    ) -> Optional[DocumentContent]:
        """根据拜访记录ID获取文档内容"""
        query = select(DocumentContent).where(DocumentContent.visit_record_id == visit_record_id)
        return session.exec(query).first()
