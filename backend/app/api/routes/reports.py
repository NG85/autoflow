import logging
from typing import List, Optional, Any

import requests
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from fastapi import APIRouter, Body, HTTPException
from fastapi_pagination import Page
from datetime import datetime, timedelta

from app.api.routes.crm.models import (
    DailyReportRequest,
    DailyReportResponse,
    DailyReportStatistics,
    AssessmentDetail,
    DepartmentDailyReportResponse,
    CompanyDailyReportResponse,
    DepartmentWeeklyReportResponse,
    CompanyWeeklyReportResponse,
    WeeklyReportRequest,
    WeeklyReportStatistics,
    SalesTaskWeeklySummaryRequest,
    DepartmentSalesTaskWeeklySummaryResponse,
    CompanySalesTaskWeeklySummaryResponse,
)
from app.services.crm_statistics_service import crm_statistics_service
from app.services.crm_sales_task_statistics_service import crm_sales_task_statistics_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports")

@router.post("/daily-reports", response_model=Page[DailyReportResponse])
def get_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: DailyReportRequest,
):
    """
    获取销售个人日报信息
    
    返回销售人员的日报统计数据，包括：
    - 记录人/销售人员
    - 部门名称
    - 报告日期
    - 统计数据（客户跟进、合作伙伴跟进、评估等级统计）
    - 拜访记录详情页面链接
    
    支持的查询条件：
    - sales_id: 精确匹配销售人员ID
    - sales_name: 模糊匹配销售人员姓名
    - start_date/end_date: 日期范围过滤
    - department_name: 精确匹配部门名称
    - 分页参数: page, page_size
    """
    try:
        # 如果没有指定日期范围，使用最近7天的数据
        if not request.start_date and not request.end_date:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        else:
            start_date = request.start_date or (datetime.now().date() - timedelta(days=7))
            end_date = request.end_date or datetime.now().date()
        
        # 日报口径来自 CRMAccountOpportunityAssessment：
        # 通过 crm_statistics_service.get_sales_complete_daily_report 组装销售维度的完整日报
        from app.core.config import settings

        # 边界保护：如果 start_date > end_date，则交换
        if start_date and end_date and start_date > end_date:
            start_date, end_date = end_date, start_date

        all_reports: list[DailyReportResponse] = []
        current_date = start_date
        while current_date <= end_date:
            daily_reports = crm_statistics_service.get_sales_complete_daily_report(
                session=db_session,
                target_date=current_date,
            )

            for report in daily_reports:
                recorder_id = str(report.get("recorder_id") or "")
                recorder_name = str(report.get("recorder") or "")
                department_name = str(report.get("department") or "")

                # 应用过滤条件
                if request.sales_id and recorder_id != request.sales_id:
                    continue
                if request.sales_name and request.sales_name.lower() not in recorder_name.lower():
                    continue
                if request.department_name and department_name != request.department_name:
                    continue

                report_date = report.get("report_date") or current_date
                visit_detail_page = str(report.get("visit_detail_page") or "")
                account_list_page = (
                    f"{settings.ACCOUNT_LIST_PAGE_URL}?department={department_name}"
                    if department_name
                    else settings.ACCOUNT_LIST_PAGE_URL
                )

                # 兼容 OpportunityAssessment 明细字段：opportunity_name -> opportunity_names
                first_assessments = report.get("first_assessment") or []
                multi_assessments = report.get("multi_assessment") or []
                for a in first_assessments:
                    if isinstance(a, dict) and "opportunity_names" not in a:
                        a["opportunity_names"] = a.get("opportunity_name") or ""
                for a in multi_assessments:
                    if isinstance(a, dict) and "opportunity_names" not in a:
                        a["opportunity_names"] = a.get("opportunity_name") or ""

                all_reports.append(
                    DailyReportResponse(
                        recorder=recorder_name,
                        department_name=department_name,
                        report_date=report_date,
                        statistics=[
                            DailyReportStatistics(
                                end_customer_total_follow_up=report.get("end_customer_total_follow_up", 0),
                                end_customer_total_first_visit=report.get("end_customer_total_first_visit", 0),
                                end_customer_total_multi_visit=report.get("end_customer_total_multi_visit", 0),
                                partner_total_follow_up=report.get("partner_total_follow_up", 0),
                                # 合作伙伴不区分首次/多次，使用默认值0
                                # 首次拜访的红黄绿灯统计（包含客户和合作伙伴）
                                first_visit_red_count=report.get("first_visit_red_count", 0),
                                first_visit_yellow_count=report.get("first_visit_yellow_count", 0),
                                first_visit_green_count=report.get("first_visit_green_count", 0),
                                # 多次跟进的红黄绿灯统计（仅客户）
                                multi_visit_red_count=report.get("multi_visit_red_count", 0),
                                multi_visit_yellow_count=report.get("multi_visit_yellow_count", 0),
                                multi_visit_green_count=report.get("multi_visit_green_count", 0),
                                # 合作伙伴的红黄绿灯统计（不区分首次/多次）
                                partner_red_count=report.get("partner_red_count", 0),
                                partner_yellow_count=report.get("partner_yellow_count", 0),
                                partner_green_count=report.get("partner_green_count", 0),
                                # 兼容旧字段（如果存在则使用，否则使用新字段的汇总值）
                                assessment_red_count=report.get("assessment_red_count") or (
                                    report.get("first_visit_red_count", 0) + 
                                    report.get("multi_visit_red_count", 0) + 
                                    report.get("partner_red_count", 0)
                                ),
                                assessment_yellow_count=report.get("assessment_yellow_count") or (
                                    report.get("first_visit_yellow_count", 0) + 
                                    report.get("multi_visit_yellow_count", 0) + 
                                    report.get("partner_yellow_count", 0)
                                ),
                                assessment_green_count=report.get("assessment_green_count") or (
                                    report.get("first_visit_green_count", 0) + 
                                    report.get("multi_visit_green_count", 0) + 
                                    report.get("partner_green_count", 0)
                                ),
                            )
                        ],
                        visit_detail_page=visit_detail_page,
                        account_list_page=account_list_page,
                        first_assessment=[AssessmentDetail(**a) for a in first_assessments],
                        multi_assessment=[AssessmentDetail(**a) for a in multi_assessments],
                    )
                )

            current_date += timedelta(days=1)

        # 按日期降序排序
        all_reports.sort(key=lambda x: x.report_date, reverse=True)

        # 手动分页
        total = len(all_reports)
        start_idx = (request.page - 1) * request.page_size
        end_idx = start_idx + request.page_size
        items = all_reports[start_idx:end_idx]

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


@router.post("/department-daily-reports", response_model=List[DepartmentDailyReportResponse])
def get_department_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天")
):
    """
    获取部门日报数据
    
    按部门汇总销售人员的日报数据：
    1. 统计数据直接加和
    2. 评估数据直接合并
    3. 包含各销售的详细日报信息
    
    Args:
        target_date: 目标日期，格式YYYY-MM-DD
        
    Returns:
        部门日报数据列表
    """
    try:
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD格式")
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 查询 {parsed_date} 的部门日报数据")
        
        # 获取部门汇总报告
        department_reports = crm_statistics_service.aggregate_department_reports(
            session=db_session,
            target_date=parsed_date
        )
        
        if not department_reports:
            logger.warning(f"{parsed_date} 没有找到任何部门日报数据")
            return []
        
        # 转换为响应格式
        from app.core.config import settings
        response_reports = []
        for report in department_reports:
            # 转换日期格式
            if hasattr(report.get('report_date'), 'isoformat'):
                report['report_date'] = report['report_date'].isoformat()
            
            # 添加必需的字段（与推送逻辑保持一致）
            department_name = report.get('department_name', '')
            report['visit_detail_page'] = (
                f"{settings.VISIT_DETAIL_PAGE_URL}"
                f"?start_date={parsed_date}&end_date={parsed_date}"
            )
            report['account_list_page'] = (
                f"{settings.ACCOUNT_LIST_PAGE_URL}?department={department_name}"
                if department_name
                else settings.ACCOUNT_LIST_PAGE_URL
            )
            
            # 构造响应对象
            department_response = DepartmentDailyReportResponse(**report)
            response_reports.append(department_response)
        
        logger.info(f"成功返回 {len(response_reports)} 个部门的日报数据")
        return response_reports
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取部门日报数据失败: {e}")
        raise InternalServerError()


@router.post("/company-daily-reports", response_model=CompanyDailyReportResponse)
def get_company_daily_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天")
):
    """
    获取公司日报数据
    
    汇总全公司销售人员的日报数据：
    1. 统计数据直接加和
    2. 评估数据直接合并（但不包含跟进记录字段，且过滤绿灯评估）
    3. 提供公司级的整体数据概览
    
    注意：推送逻辑根据应用环境区分
    - 内部环境：推送到匹配的群聊
    - 外部环境：推送给外部管理员
    
    Args:
        target_date: 目标日期，格式YYYY-MM-DD
        
    Returns:
        公司日报数据
    """
    try:
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD格式")
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 查询 {parsed_date} 的公司日报数据")
        
        # 获取公司汇总报告
        company_report = crm_statistics_service.aggregate_company_report(
            session=db_session,
            target_date=parsed_date
        )
        
        if not company_report:
            logger.warning(f"{parsed_date} 没有找到任何公司日报数据")
            raise HTTPException(status_code=400, detail=f"{parsed_date} 没有找到任何数据")
        
        # 转换日期格式
        if hasattr(company_report.get('report_date'), 'isoformat'):
            company_report['report_date'] = company_report['report_date'].isoformat()
        
        # 添加必需的字段（与推送逻辑保持一致）
        from app.core.config import settings
        company_report['visit_detail_page'] = (
            f"{settings.VISIT_DETAIL_PAGE_URL}"
            f"?start_date={parsed_date}&end_date={parsed_date}"
        )
        company_report['account_list_page'] = settings.ACCOUNT_LIST_PAGE_URL
        
        # 构造响应对象
        company_response = CompanyDailyReportResponse(**company_report)
        
        logger.info(f"成功返回公司日报数据")
        return company_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取公司日报数据失败: {e}")
        raise InternalServerError()


@router.post("/department-weekly-reports", response_model=List[DepartmentWeeklyReportResponse])
def get_department_weekly_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取团队周报数据
    
    从 Aldebaran 接口获取周报数据
    
    Args:
        request: 周报查询请求，包含部门名称和日期范围
        
    Returns:
        团队周报数据列表
    """
    try:
        from app.core.config import settings
        
        # 解析日期范围
        if request.start_date and request.end_date:
            start_date = request.start_date
            end_date = request.end_date
        else:
            # 默认查询最近一周的数据
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        
        logger.info(f"用户 {user.id} 查询 {start_date} 到 {end_date} 的团队周报数据")
        
        # 计算 report_year 和 report_week_of_year（按周日-周六口径，使用周六的 ISO week）
        iso_year, iso_week, _ = end_date.isocalendar()
        report_year = int(iso_year)
        report_week_of_year = int(iso_week)
        
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

            if department_name:
                result.setdefault("department_name", department_name)

            return result
        
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
            
            # 添加响应模型需要的字段
            dept_report["report_start_date"] = start_date
            dept_report["report_end_date"] = end_date
            dept_report["visit_detail_page"] = (
                f"{settings.VISIT_DETAIL_PAGE_URL}"
                f"?start_date={start_date}&end_date={end_date}"
            )
            dept_report["account_list_page"] = (
                f"{settings.ACCOUNT_LIST_PAGE_URL}?department={department_name}"
                if department_name
                else settings.ACCOUNT_LIST_PAGE_URL
            )
            
            # 添加默认的 statistics（响应模型需要但 Aldebaran 接口不返回）
            dept_report["statistics"] = [
                WeeklyReportStatistics(
                    end_customer_total_follow_up=0,
                    end_customer_total_first_visit=0,
                    end_customer_total_multi_visit=0,
                    partner_total_follow_up=0,
                    partner_total_first_visit=0,
                    partner_total_multi_visit=0,
                    assessment_red_count=0,
                    assessment_yellow_count=0,
                    assessment_green_count=0,
                    end_customer_avg_follow_up="0",
                    partner_avg_follow_up="0",
                )
            ]
            
            # 处理 sales_quadrants（如果存在）
            if "sales_quadrants" in dept_report:
                sq = dept_report.get("sales_quadrants")
                if isinstance(sq, dict):
                    # 如果已经是字典格式，转换为列表格式
                    dept_report["sales_quadrants"] = {
                        "behavior_hh": sq.get("behavior_hh", []) if isinstance(sq.get("behavior_hh"), list) else [],
                        "behavior_hl": sq.get("behavior_hl", []) if isinstance(sq.get("behavior_hl"), list) else [],
                        "behavior_lh": sq.get("behavior_lh", []) if isinstance(sq.get("behavior_lh"), list) else [],
                        "behavior_ll": sq.get("behavior_ll", []) if isinstance(sq.get("behavior_ll"), list) else [],
                    }
                else:
                    dept_report["sales_quadrants"] = None
            
            department_reports.append(dept_report)
        
        if not department_reports:
            logger.warning(f"{start_date} 到 {end_date} 没有找到任何团队周报数据")
            return []
        
        # 转换为响应格式
        response_reports = []
        for report in department_reports:
            # 转换日期格式
            if hasattr(report.get('report_start_date'), 'isoformat'):
                report['report_start_date'] = report['report_start_date'].isoformat()
            if hasattr(report.get('report_end_date'), 'isoformat'):
                report['report_end_date'] = report['report_end_date'].isoformat()
            
            # 构造响应对象
            try:
                department_response = DepartmentWeeklyReportResponse(**report)
                response_reports.append(department_response)
            except Exception as e:
                logger.warning(f"构造部门周报响应对象失败，跳过该部门: {report.get('department_name', 'Unknown')}, err={e}")
                continue
        
        logger.info(f"成功返回 {len(response_reports)} 个团队的周报数据")
        return response_reports
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取团队周报数据失败: {e}")
        raise InternalServerError()


@router.post("/company-weekly-reports", response_model=CompanyWeeklyReportResponse)
def get_company_weekly_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取公司周报数据
    
    从 Aldebaran 接口获取周报数据
    
    Args:
        request: 周报查询请求，包含日期范围
        
    Returns:
        公司周报数据
    """
    try:
        from app.core.config import settings
        
        # 解析日期范围
        if request.start_date and request.end_date:
            start_date = request.start_date
            end_date = request.end_date
        else:
            # 默认查询最近一周的数据
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        
        logger.info(f"用户 {user.id} 查询 {start_date} 到 {end_date} 的公司周报数据")
        
        # 计算 report_year 和 report_week_of_year（按周日-周六口径，使用周六的 ISO week）
        iso_year, iso_week, _ = end_date.isocalendar()
        report_year = int(iso_year)
        report_week_of_year = int(iso_week)
        
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
        
        # 获取公司周报数据
        try:
            company_weekly_report = _aldebaran_fetch_weekly_report(
                report_year=report_year,
                report_week_of_year=report_week_of_year,
                department_name=None,
            )
        except Exception as e:
            logger.error(f"获取公司周报失败: err={e}")
            raise HTTPException(
                status_code=500,
                detail=f"获取公司周报数据失败: {str(e)}"
            )
        
        # 添加响应模型需要的字段
        company_weekly_report["report_start_date"] = start_date
        company_weekly_report["report_end_date"] = end_date
        company_weekly_report["visit_detail_page"] = (
            f"{settings.VISIT_DETAIL_PAGE_URL}"
            f"?start_date={start_date}&end_date={end_date}"
        )
        company_weekly_report["account_list_page"] = settings.ACCOUNT_LIST_PAGE_URL
        
        # 添加默认的 statistics（响应模型需要但 Aldebaran 接口不返回）
        company_weekly_report["statistics"] = [
            WeeklyReportStatistics(
                end_customer_total_follow_up=0,
                end_customer_total_first_visit=0,
                end_customer_total_multi_visit=0,
                partner_total_follow_up=0,
                partner_total_first_visit=0,
                partner_total_multi_visit=0,
                assessment_red_count=0,
                assessment_yellow_count=0,
                assessment_green_count=0,
                end_customer_avg_follow_up="0",
                partner_avg_follow_up="0",
            )
        ]
        
        # 处理 sales_quadrants（如果存在）
        if "sales_quadrants" in company_weekly_report:
            sq = company_weekly_report.get("sales_quadrants")
            if isinstance(sq, dict):
                # 如果已经是字典格式，确保是列表格式
                company_weekly_report["sales_quadrants"] = {
                    "behavior_hh": sq.get("behavior_hh", []) if isinstance(sq.get("behavior_hh"), list) else [],
                    "behavior_hl": sq.get("behavior_hl", []) if isinstance(sq.get("behavior_hl"), list) else [],
                    "behavior_lh": sq.get("behavior_lh", []) if isinstance(sq.get("behavior_lh"), list) else [],
                    "behavior_ll": sq.get("behavior_ll", []) if isinstance(sq.get("behavior_ll"), list) else [],
                }
            else:
                company_weekly_report["sales_quadrants"] = None
        
        # 转换日期格式
        if hasattr(company_weekly_report.get('report_start_date'), 'isoformat'):
            company_weekly_report['report_start_date'] = company_weekly_report['report_start_date'].isoformat()
        if hasattr(company_weekly_report.get('report_end_date'), 'isoformat'):
            company_weekly_report['report_end_date'] = company_weekly_report['report_end_date'].isoformat()
        
        # 构造响应对象
        try:
            company_response = CompanyWeeklyReportResponse(**company_weekly_report)
            logger.info(f"成功生成公司周报数据，日期范围: {start_date} 到 {end_date}")
            return company_response
        except Exception as e:
            logger.exception(f"构造公司周报响应对象失败: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"构造公司周报响应对象失败: {str(e)}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"查询公司周报数据失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"查询公司周报数据失败: {str(e)}"
        )


@router.post(
    "/department-weekly-tasks",
    response_model=List[DepartmentSalesTaskWeeklySummaryResponse],
)
def get_department_sales_task_weekly_summary(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: SalesTaskWeeklySummaryRequest,
):
    """
    销售任务周报：按部门汇总（department_id）。

    数据来源：crm_todos_weekly_metrics（由定时任务写入）
    """
    try:
        if request.start_date and request.end_date:
            week_start = request.start_date
            week_end = request.end_date
        else:
            # 默认最近一周：与 send_sales_task_summary 口径一致（上周日到本周六）
            today = datetime.now().date()
            days_since_sunday = (today.weekday() + 1) % 7
            week_start = today - timedelta(days=days_since_sunday + 7)
            week_end = week_start + timedelta(days=6)

        logger.info(f"用户 {user.id} 查询销售任务部门周报，范围: {week_start} 到 {week_end}")

        reports = crm_sales_task_statistics_service.aggregate_department_weekly_summary(
            session=db_session,
            week_start=week_start,
            week_end=week_end,
            department_id=request.department_id,
        )
        return reports
    except Exception as e:
        logger.exception(f"查询销售任务部门周报失败: {e}")
        raise InternalServerError()


@router.post(
    "/company-weekly-tasks",
    response_model=CompanySalesTaskWeeklySummaryResponse,
)
def get_company_sales_task_weekly_summary(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: SalesTaskWeeklySummaryRequest,
):
    """
    销售任务周报：公司总计（全公司）。

    数据来源：crm_todos_weekly_metrics（由定时任务写入）
    """
    try:
        if request.start_date and request.end_date:
            week_start = request.start_date
            week_end = request.end_date
        else:
            today = datetime.now().date()
            days_since_sunday = (today.weekday() + 1) % 7
            week_start = today - timedelta(days=days_since_sunday + 7)
            week_end = week_start + timedelta(days=6)

        logger.info(f"用户 {user.id} 查询销售任务公司周报，范围: {week_start} 到 {week_end}")

        report = crm_sales_task_statistics_service.aggregate_company_weekly_summary(
            session=db_session,
            week_start=week_start,
            week_end=week_end,
        )
        if not report:
            raise HTTPException(status_code=400, detail="未找到该周的销售任务周报数据")
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"查询销售任务公司周报失败: {e}")
        raise InternalServerError()