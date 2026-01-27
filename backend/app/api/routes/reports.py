import logging
from typing import List, Optional, Any

import requests
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from fastapi import APIRouter, HTTPException
from fastapi_pagination import Page
from datetime import datetime, timedelta

from app.api.routes.crm.models import (
    DailyReportRequest,
    WeeklyReportRequest,
)
from app.services.crm_statistics_service import crm_statistics_service
from app.services.crm_sales_task_statistics_service import crm_sales_task_statistics_service
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports")

@router.post("/daily-reports/sales")
def get_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: DailyReportRequest,
):
    """获取销售个人日报信息"""
    try:        
        # 解析目标日期
        if request.report_date:            
            parsed_date = request.report_date
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        # 日报口径来自 CRMAccountOpportunityAssessment：
        # 通过 crm_statistics_service.get_sales_complete_daily_report 组装销售维度的完整日报

        daily_reports = crm_statistics_service.get_sales_complete_daily_report(
            session=db_session,
            target_date=parsed_date,
        )

        # 应用人员筛选条件
        if request.sales_id or request.sales_name:
            filtered_reports = []
            for report in daily_reports:
                recorder_id = str(report.get("recorder_id") or "")
                recorder_name = str(report.get("recorder") or "")
                
                # 按销售人员ID筛选
                if request.sales_id and recorder_id != request.sales_id:
                    continue
                
                # 按销售人员姓名筛选
                if request.sales_name and recorder_name.lower() != request.sales_name.lower():
                    continue
                
                filtered_reports.append(report)
            daily_reports = filtered_reports

        # 按姓名降序排序
        daily_reports.sort(key=lambda x: x.get('recorder'), reverse=True)

        # 手动分页
        total = len(daily_reports)
        start_idx = (request.page - 1) * request.page_size
        end_idx = start_idx + request.page_size
        items = daily_reports[start_idx:end_idx]

        total_pages = (total + request.page_size - 1) // request.page_size
        
        return Page(
            items=items,
            total=total,
            page=request.page,
            size=request.page_size,
            pages=total_pages
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/daily-reports/department")
def get_department_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: DailyReportRequest,
):
    """获取部门日报数据"""
    try:
        # 解析目标日期
        if request.report_date:
            parsed_date = request.report_date
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
                
        # 获取部门汇总报告
        department_reports = crm_statistics_service.aggregate_department_reports(
            session=db_session,
            target_date=parsed_date
        )
        
        if not department_reports:
            logger.warning(f"{parsed_date} 没有找到任何部门日报数据")
            return []
        
        # 应用部门筛选条件
        if request.department_name:
            filtered_reports = []
            for report in department_reports:
                if report.get('department_name') != request.department_name:
                    continue
                filtered_reports.append(report)
            department_reports = filtered_reports

        # 按姓名降序排序
        department_reports.sort(key=lambda x: x.get('department_name'), reverse=True)

        # 手动分页
        total = len(department_reports)
        start_idx = (request.page - 1) * request.page_size
        end_idx = start_idx + request.page_size
        items = department_reports[start_idx:end_idx]

        total_pages = (total + request.page_size - 1) // request.page_size
        
        return Page(
            items=items,
            total=total,
            page=request.page,
            size=request.page_size,
            pages=total_pages
        )        
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取部门日报数据失败: {e}")
        raise InternalServerError()


@router.post("/daily-reports/company")
def get_company_daily_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: DailyReportRequest,
):
    """获取公司日报数据"""
    try:
        # 解析目标日期
        if request.report_date:
            parsed_date = request.report_date
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        # 获取公司汇总报告
        company_report = crm_statistics_service.aggregate_company_report(
            session=db_session,
            target_date=parsed_date
        )
        
        if not company_report:
            logger.warning(f"{parsed_date} 没有找到任何公司日报数据")
            raise HTTPException(status_code=400, detail=f"{parsed_date} 没有找到任何数据")
        
        return company_report
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取公司日报数据失败: {e}")
        raise InternalServerError()


def _aldebaran_fetch_weekly_report(
    *,
    report_year: int,
    report_week_of_year: int,
    department_name: Optional[str],
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """调用 Aldebaran 周报接口获取周报内容"""
    base_url = settings.ALDEBARAN_BASE_URL.rstrip("/")
    url = f"{base_url}/api/v1/report/weekly"
    payload = {
        "tenant_id": 'PINGCAP',
        "report_year": int(report_year),
        "report_week_of_year": int(report_week_of_year),
        "department": department_name,
    }

    logger.info(
        "调用 Aldebaran 周报接口: %s, payload=%s",
        url,
        {
            "tenant_id": payload["tenant_id"],
            "report_year": payload["report_year"],
            "report_week_of_year": payload["report_week_of_year"],
            "department": payload["department"],
        },
    )

    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=timeout_seconds)
    if resp.status_code != 200:
        raise RuntimeError(f"Aldebaran weekly report http {resp.status_code}: {resp.text}")

    data = resp.json()

    if isinstance(data, dict) and data.get("status") == "success":
        report_data = data.get("data")
        if not isinstance(report_data, dict):
            raise RuntimeError(f"Aldebaran weekly report missing data: {data}")
        result = report_data
    else:
        raise RuntimeError(f"Aldebaran weekly report invalid json: {data}")

    return result

@router.post("/weekly-reports/department")
def get_department_weekly_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取团队周报数据
    
    从 Aldebaran 接口获取周报数据
    
    Args:
        request: 周报查询请求，包含部门名称和报告日期
        
    Returns:
        团队周报数据列表
    """
    try:
        from app.core.config import settings
        
        # 解析日期范围
        if request.report_date:
            parsed_date = request.report_date
        else:
            # 没有设置 report_date 时，选择当前日期往前最近的一个周六
            today = datetime.now().date()
            weekday = today.weekday()  # 周一为0，周六为5，周日为6
            days_to_last_saturday = (weekday - 5) % 7
            parsed_date = today - timedelta(days=days_to_last_saturday)
        
        # 计算 report_year 和 report_week_of_year（按周日-周六口径，使用周六的 ISO week）
        iso_year, iso_week, _ = parsed_date.isocalendar()
        report_year = int(iso_year)
        report_week_of_year = int(iso_week)        
        
        # 获取部门列表（如果指定了部门名称，只查询该部门；否则查询所有部门）
        if request.department_name:
            department_names = [request.department_name]
        else:
            # 获取所有有负责人的部门
            departments_with_managers = crm_statistics_service._get_departments_with_managers_from_oauth()
            if not departments_with_managers:
                from app.repositories.user_profile import user_profile_repo
                dept_managers = user_profile_repo.get_all_departments_with_managers(db_session)
                department_names = list(dept_managers.keys()) if dept_managers else []
            else:
                department_names = list(departments_with_managers.keys())
        
        # 获取部门周报数据
        department_reports = []
        for department_name in department_names:
            try:
                dept_report = _aldebaran_fetch_weekly_report(
                    report_year=report_year,
                    report_week_of_year=report_week_of_year,
                    department_name=department_name,
                )
            except Exception as e:
                logger.warning(f"获取部门周报失败: department={department_name}, err={e}")
                continue
            
            
            department_reports.append(dept_report)
        
        if not department_reports:
            logger.warning(f"{parsed_date} 没有找到任何团队周报数据")
            return []

        return department_reports
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取团队周报数据失败: {e}")
        raise InternalServerError()


@router.post("/weekly-reports/company")
def get_company_weekly_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取公司周报数据
    
    从 Aldebaran 接口获取周报数据
    
    Args:
        request: 周报查询请求，包含报告日期
        
    Returns:
        公司周报数据
    """
    try:
        from app.core.config import settings
        
        # 解析日期范围
        if request.report_date:
            parsed_date = request.report_date
        else:
            # 没有设置 report_date 时，选择当前日期往前最近的一个周六
            today = datetime.now().date()
            weekday = today.weekday()  # 周一为0，周六为5，周日为6
            days_to_last_saturday = (weekday - 5) % 7
            parsed_date = today - timedelta(days=days_to_last_saturday)
        
        # 计算 report_year 和 report_week_of_year（按周日-周六口径，使用周六的 ISO week）
        iso_year, iso_week, _ = parsed_date.isocalendar()
        report_year = int(iso_year)
        report_week_of_year = int(iso_week)        
        
        # 获取公司周报数据
        company_weekly_report = _aldebaran_fetch_weekly_report(
            report_year=report_year,
            report_week_of_year=report_week_of_year,
            department_name=None,
        )
        
        return company_weekly_report
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"查询公司周报数据失败: {e}")
        raise InternalServerError()
