from typing import List, Optional
from datetime import date
from sqlmodel import Session, select, func, and_
from app.repositories.base_repo import BaseRepo
from app.models.crm_daily_account_statistics import CRMDailyAccountStatistics
from app.api.routes.crm.models import DailyReportRequest


class CRMDailyAccountStatisticsRepo(BaseRepo):
    model_cls = CRMDailyAccountStatistics
    
    def get_daily_reports(
        self, 
        session: Session, 
        request: DailyReportRequest
    ) -> tuple[List[CRMDailyAccountStatistics], int]:
        """
        获取销售个人日报数据
        
        Args:
            session: 数据库会话
            request: 查询请求参数
            
        Returns:
            tuple: (数据列表, 总数)
        """
        # 构建基础查询
        query = select(CRMDailyAccountStatistics)
        count_query = select(func.count(CRMDailyAccountStatistics.id))
        
        # 添加过滤条件
        filters = []
        
        if request.sales_id:
            filters.append(CRMDailyAccountStatistics.sales_id == request.sales_id)
            
        if request.sales_name:
            # 支持模糊查询
            filters.append(CRMDailyAccountStatistics.sales_name.like(f"%{request.sales_name}%"))
            
        if request.start_date:
            filters.append(CRMDailyAccountStatistics.report_date >= request.start_date)
            
        if request.end_date:
            filters.append(CRMDailyAccountStatistics.report_date <= request.end_date)
            
        if request.department_name:
            filters.append(CRMDailyAccountStatistics.department_name == request.department_name)
        
        if filters:
            query = query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))
        
        # 添加排序 - 按日期降序
        query = query.order_by(CRMDailyAccountStatistics.report_date.desc())
        
        # 获取总数
        total = session.exec(count_query).one()
        
        # 添加分页
        offset = (request.page - 1) * request.page_size
        query = query.offset(offset).limit(request.page_size)
        
        # 执行查询
        results = session.exec(query).all()
        
        return results, total
    



# 创建repository实例
crm_daily_account_statistics_repo = CRMDailyAccountStatisticsRepo()
