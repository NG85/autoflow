import logging
from sqlmodel import Session, select
from typing import Optional, List
from datetime import date
from app.models.crm_report_index import CRMReportIndex
from app.repositories.base_repo import BaseRepo

logger = logging.getLogger(__name__)


class CRMReportIndexRepo(BaseRepo):
    """CRM报告索引仓库"""
    
    def get_weekly_report(
        self, 
        session: Session, 
        report_type: str, 
        report_week_of_year: int, 
        report_year: int, 
        department_name: Optional[str] = None
    ) -> Optional[CRMReportIndex]:
        """
        获取周报报告
        
        Args:
            session: 数据库会话
            report_type: 报告类型，如review1s, review5s
            report_week_of_year: 报告年份中的第几周
            report_year: 报告年份
            department_name: 部门名称，None表示公司级报告
            
        Returns:
            CRMReportIndex: 报告索引记录，如果未找到返回None
        """
        try:
            # 构建基础查询条件
            conditions = [
                CRMReportIndex.report_type == report_type,
                CRMReportIndex.report_calendar_type == 'Weekly',
                CRMReportIndex.report_status == 'published',
                CRMReportIndex.report_week_of_year == report_week_of_year,
                CRMReportIndex.report_year == report_year
            ]
            
            # 根据是否提供部门名称添加条件
            if department_name is not None:
                conditions.append(CRMReportIndex.department_name == department_name)
            else:
                conditions.append(CRMReportIndex.department_name.is_(None))
            
            statement = select(CRMReportIndex).where(*conditions)
            result = session.exec(statement).first()
            return result
            
        except Exception as e:
            report_scope = f"部门 {department_name}" if department_name else "公司"
            logger.error(f"查询{report_scope}周报报告失败: {e}")
            return None
    
    def get_weekly_report_by_department(
        self, 
        session: Session, 
        report_type: str, 
        report_week_of_year: int, 
        report_year: int, 
        department_name: str
    ) -> Optional[CRMReportIndex]:
        """
        根据部门获取周报报告
        
        Args:
            session: 数据库会话
            report_type: 报告类型，如review1s, review5s
            report_week_of_year: 报告年份中的第几周
            report_year: 报告年份
            department_name: 部门名称
            
        Returns:
            CRMReportIndex: 报告索引记录，如果未找到返回None
        """
        return self.get_weekly_report(session, report_type, report_week_of_year, report_year, department_name)
    
    def get_weekly_report_by_company(
        self, 
        session: Session, 
        report_type: str, 
        report_week_of_year: int, 
        report_year: int
    ) -> Optional[CRMReportIndex]:
        """
        获取公司级周报报告
        
        Args:
            session: 数据库会话
            report_type: 报告类型，如review1s, review5s
            report_week_of_year: 报告年份中的第几周
            report_year: 报告年份
            
        Returns:
            CRMReportIndex: 报告索引记录，如果未找到返回None
        """
        return self.get_weekly_report(session, report_type, report_week_of_year, report_year, None)
