import logging
from datetime import datetime, timedelta
from enum import Enum
from sqlmodel import Session, select
import pytz
from typing import Any, Optional

from urllib.parse import quote_plus

from app.core.config import settings, WritebackMode, WritebackFrequency
from app.core.db import engine
from app.celery import app
from app.models import DataSource, DataSourceType
from app.repositories import knowledge_base_repo
from app.services.aldebaran_service import aldebaran_client
from app.services.crm_statistics_service import crm_statistics_service
from app.services.oauth_service import oauth_client
from app.services.platform_notification_service import platform_notification_service
from app.services.crm_writeback_service import crm_writeback_service
from app.services.crm_sales_task_statistics_service import crm_sales_task_statistics_service
from app.services.crm_weekly_followup_service import crm_weekly_followup_service
from app.tasks.knowledge_base import import_documents_from_kb_datasource
from app.utils.date_utils import beijing_today_date

logger = logging.getLogger(__name__)


class TodoDataSourceType(str, Enum):
    """TODO数据源类型枚举"""
    MANUAL = "MANUAL"
    AI_EXTRACTION = "AI_EXTRACTION"
    PIPELINE_PLAYBOOK = "PIPELINE_PLAYBOOK"
    
    def display_name(self) -> str:
        """转换为显示名称"""
        display_names = {
            TodoDataSourceType.MANUAL: "自创建",
            TodoDataSourceType.AI_EXTRACTION: "AI抽取",
            TodoDataSourceType.PIPELINE_PLAYBOOK: "销售打法",
        }
        return display_names.get(self, self.value)

@app.task(bind=True, max_retries=3)
def create_crm_daily_datasource(self):
    """
    创建CRM每日数据源并导入文档
    每天在配置的时间执行，默认10:00
    """
    try:
        # 获取昨天的日期
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y-%m-%d')
        today_str = now.strftime('%Y-%m-%d')
        
        # 构建数据源名称
        execution_time = now.strftime('%Y%m%d%H%M')
        datasource_name = f"CRM_{yesterday_str}_{execution_time}"
        
        # 构建过滤条件，只包含昨天的数据
        increment_filter = f"last_modified_time >= '{yesterday_str}' AND last_modified_time < '{today_str}'"
        # 构建过滤条件，是否忽略account主表
        if settings.CRM_ACCOUNT_PRIMARY_EXCLUDE:
            account_filter = f"unique_id = '1234567890'"
        else:
            account_filter = None
        
        logger.info(f"开始创建CRM数据源，时间范围: {yesterday_str} 00:00:00 到 {today_str} 00:00:00")
        
        with Session(engine) as session:
            # 获取知识库
            kb = knowledge_base_repo.must_get(session, settings.CRM_DAILY_KB_ID)
            
            # 创建数据源
            new_data_source = DataSource(
                name=datasource_name,
                description=f"CRM data from {yesterday_str}, executed at {execution_time}",
                data_source_type=DataSourceType.CRM,
                config=[{
                    "increment_filter": increment_filter,
                    "account_filter": account_filter,
                    "batch_size": 30
                }]
            )
            
            # 添加到知识库
            new_data_source = knowledge_base_repo.add_kb_datasource(session, kb, new_data_source)
            
            logger.info(f"成功创建数据源: {datasource_name}")
            
            # 触发文档导入任务
            import_documents_from_kb_datasource.delay(
                kb_id=settings.CRM_DAILY_KB_ID,
                data_source_id=new_data_source.id
            )
            
            logger.info(f"已触发文档导入任务，数据源ID: {new_data_source.id}")
        
    except Exception as e:
        logger.error(f"创建CRM数据源失败: {str(e)}")
        # 使用Celery的重试机制
        self.retry(exc=e, countdown=60)  # 1分钟后重试


@app.task(bind=True, max_retries=3)
def generate_crm_daily_statistics(self, target_date_str=None, report_type=None):
    """
    生成CRM销售/团队/公司日报完整数据并推送飞书通知
    每天早上8:30执行，处理前一天的销售和团队日报数据
    
    Args:
        target_date_str: 目标日期字符串，格式YYYY-MM-DD，不传则默认为昨天
        report_type: 报告类型，支持 'sales'（销售个人日报） / 'department'（团队日报） / 'company'（公司日报），不传则默认为所有
    
    工作流程：
    1. 查询指定日期的拜访记录，按照销售人员分组，统计并生成完整的销售个人日报数据
    2. 基于客户/商机评估信息，补充红黄绿灯统计与评估明细，推送销售个人日报飞书卡片给每个有数据的销售人员
    3. 从 crm_department_daily_summary 表中读取部门级汇总数据，为所有有负责人的部门生成部门日报（无数据部门生成空日报）
    4. 从 crm_department_daily_summary 表中读取公司级汇总数据，生成公司日报
    5. 将部门日报和公司日报通过飞书卡片推送给对应的负责人 / 管理员
    
    """
    try:
        # 解析目标日期
        if target_date_str:
            try:
                target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
                logger.info(f"开始执行CRM日报数据生成任务，目标日期: {target_date}")
            except ValueError:
                logger.error(f"无效的日期格式: {target_date_str}")
                target_date = (datetime.now() - timedelta(days=1)).date()
                logger.info(f"使用默认日期: {target_date}")
        else:
            target_date = (datetime.now() - timedelta(days=1)).date()
            logger.info(f"开始执行CRM日报数据生成任务，默认处理昨天: {target_date}")
        
        with Session(engine) as session:
            valid_report_types = {"sales", "department", "company"}
            if report_type is not None and report_type not in valid_report_types:
                raise ValueError(f"invalid report_type={report_type}, valid={sorted(valid_report_types)}")

            triggered_types: list[str] = []
            sales_count = 0

            # 1. 生成并推送销售个人日报（仅在 sales_count > 0 时推送个人卡片）
            if not report_type or report_type == "sales":
                sales_count = crm_statistics_service.generate_sales_daily_statistics(session, target_date)
                triggered_types.append("sales")
            
            # 2. 生成并推送团队（部门）日报
            #    - 即使没有团队日报数据，也会为所有有负责人的部门生成空数据的团队日报
            if not report_type or report_type == "department":
                crm_statistics_service._generate_and_send_department_daily_reports(session, target_date)
                triggered_types.append("department")
            
            # 3. 生成并推送公司日报
            #    - 基于 crm_department_daily_summary 中的公司级汇总数据
            if not report_type or report_type == "company":
                crm_statistics_service._generate_and_send_company_daily_report(session, target_date)
                triggered_types.append("company")
            
            # 4. 生成简化的返回信息（用于任务状态查询）
            if "sales" in triggered_types and sales_count > 0:
                logger.info(
                    f"CRM日报数据生成完成，report_type={report_type or 'all'}，"
                    f"个人日报处理了 {sales_count} 个销售人员"
                )
                message = f"已生成日报 report_type={report_type or 'all'}；个人日报处理了 {sales_count} 个销售人员"
            else:
                logger.warning(
                    f"{target_date} 日报任务已执行，report_type={report_type or 'all'}；个人日报数据为 0（若选择了部门/公司日报，则仍会生成空日报）"
                )
                message = f"已生成日报 report_type={report_type or 'all'}；个人日报数据为 0（若选择了部门/公司日报，则仍会生成空日报）"
            
            # 返回简化的结果（用于任务状态查询 API）
            return {
                "target_date": target_date.isoformat(),
                "sales_count": sales_count,
                "report_type": report_type,
                "triggered_types": triggered_types,
                "message": message
            }
            
    except Exception as e:
        logger.exception(f"CRM日报数据生成任务失败: {e}")
        # 使用Celery的重试机制
        self.retry(exc=e, countdown=300)  # 5分钟后重试


@app.task(bind=True, max_retries=3)
def generate_crm_weekly_report(self, start_date_str=None, end_date_str=None, report_type=None):
    """
    生成CRM周报数据并推送给团队leader
    每周日上午11点执行，处理上周日到本周六的销售周报数据
    
    Args:
        start_date_str: 开始日期字符串，格式YYYY-MM-DD，不传则默认为上周日
        end_date_str: 结束日期字符串，格式YYYY-MM-DD，不传则默认为本周六
    
    工作流程：
    1. 计算上周日到本周六的日期范围
    2. 调用 Aldebaran 周报接口获取周报内容（公司周报：department=null；部门周报：department=部门名）
    3. 推送部门周报飞书卡片给各部门负责人
    4. 推送公司周报给管理团队
    """
    try:
        
        # 计算上周的日期范围
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                logger.info(f"开始执行CRM周报数据生成任务，日期范围: {start_date} 到 {end_date}")
            except ValueError:
                logger.error(f"无效的日期格式: start_date={start_date_str}, end_date={end_date_str}")
                return {
                    "success": False,
                    "message": "无效的日期格式",
                    "data": {}
                }
        else:
            # 默认处理上周日到本周六的数据
            today = beijing_today_date()
            # 计算上周日（今天往前推7天，然后找到最近的周日）
            days_since_sunday = (today.weekday() + 1) % 7  # 0=周一，1=周二，...，6=周日
            last_sunday = today - timedelta(days=days_since_sunday + 7)
            this_saturday = last_sunday + timedelta(days=6)
            
            start_date = last_sunday
            end_date = this_saturday
            logger.info(f"开始执行CRM周报数据生成任务，默认处理上周日到本周六: {start_date} 到 {end_date}")

        # 周报接口的 year/week：按周日-周六口径，使用周六的 ISO week 作为该周的 week_of_year
        iso_year, iso_week, _ = end_date.isocalendar()
        report_year = int(iso_year)
        report_week_of_year = int(iso_week)
        
        with Session(engine) as session:
            # 从接口获取周报数据（公司 + 各部门），不再自行查询数据库做汇总统计
            department_reports: list[dict[str, Any]] = []
            company_weekly_report: Optional[dict[str, Any]] = None
            from app.models.crm_weekly_followup_summary import CRMWeeklyFollowupSummary

            def _join_names(val: Any) -> str:
                if val is None:
                    return ""
                if isinstance(val, list):
                    return "|".join([str(x) for x in val if x is not None and str(x) != ""])
                return str(val)

            def _to_float(v: Any, default: float = 0.0) -> float:
                try:
                    if v is None:
                        return float(default)
                    return float(v)
                except Exception:
                    return float(default)

            def _fmt_rate(v: Any) -> str:
                """rate 字段拼接 %（接口返回已是百分数，如 72.8）。"""
                x = _to_float(v, 0.0)
                s = f"{x:.2f}".rstrip("0").rstrip(".")
                return f"{s}%"

            def _fmt_amount_yuan_to_wanyuan(v: Any) -> str:
                """amount 字段：元 -> 万元，并拼接 '万元' 后缀。"""
                x = _to_float(v, 0.0) / 10000.0
                s = f"{x:.2f}".rstrip("0").rstrip(".")
                return f"{s}万元"

            def _fmt_change(v: Any, direction: Any) -> str:
                """change 字段：根据 direction=increase/decrease 拼接 +/- 前缀。"""
                x = abs(_to_float(v, 0.0))
                s = f"{x:.2f}".rstrip("0").rstrip(".")
                if direction == "increase" or direction == "no_change":
                    return f"+{s}"
                if direction == "decrease":
                    return f"-{s}"
                return s

            def _fmt_rate_change(v: Any, direction: Any) -> str:
                """rate_change 字段：在变化值后拼接 '%'。"""
                return f"{_fmt_change(v, direction)}%"

            def _fmt_amount_change_yuan_to_wanyuan(v: Any, direction: Any) -> str:
                """金额变化字段：元 -> 万元（固定两位小数），并拼接 +/- 前缀 + '万元' 后缀。"""
                x = abs(_to_float(v, 0.0)) / 10000.0
                s = f"{x:.2f}"  # 固定保留两位小数
                if direction == "increase" or direction == "no_change":
                    return f"+{s}万元"
                if direction == "decrease":
                    return f"-{s}万元"
                return f"{s}万元"

            def _rebuild_weekly_report_for_template(raw: Any, department: Optional[str]) -> dict[str, Any]:
                """
                将周报接口返回结构重组为模板变量结构
                """
                if not isinstance(raw, dict):
                    # 极端兜底：返回空结构
                    return _build_empty_department_weekly_report(department or "")

                # performance_overview
                perf_overview = raw.get("performance_overview") if isinstance(raw.get("performance_overview"), dict) else {}
                quarterly = perf_overview.get("quarterly_achievement") if isinstance(perf_overview.get("quarterly_achievement"), dict) else {}
                weekly_deals = perf_overview.get("weekly_closed_deals") if isinstance(perf_overview.get("weekly_closed_deals"), dict) else {}
                weekly_summary = weekly_deals.get("summary") if isinstance(weekly_deals.get("summary"), dict) else {}
                deals_formatted_raw = weekly_deals.get("deals_formatted")
                # deals_formatted: 接口返回 list[str]；模板侧需要文本（按行展示）
                if isinstance(deals_formatted_raw, list):
                    deals_formatted = "\n".join([str(x) for x in deals_formatted_raw if x is not None and str(x).strip()])
                else:
                    deals_formatted = str(deals_formatted_raw or "").strip()

                # performance_forecast
                perf_forecast = raw.get("performance_forecast") if isinstance(raw.get("performance_forecast"), dict) else {}
                forecast_metrics = perf_forecast.get("forecast_metrics") if isinstance(perf_forecast.get("forecast_metrics"), dict) else {}
                closed = forecast_metrics.get("closed") if isinstance(forecast_metrics.get("closed"), dict) else {}
                commit = forecast_metrics.get("commit") if isinstance(forecast_metrics.get("commit"), dict) else {}
                upside = forecast_metrics.get("upside") if isinstance(forecast_metrics.get("upside"), dict) else {}

                # opportunity_progress
                opp = raw.get("opportunity_progress") if isinstance(raw.get("opportunity_progress"), dict) else {}
                stage_changes = opp.get("stage_changes") if isinstance(opp.get("stage_changes"), dict) else {}
                stage_progress_details_raw = stage_changes.get("stage_progress_details")

                stage_progress_details: list[dict[str, Any]] = []
                if isinstance(stage_progress_details_raw, list) and stage_progress_details_raw:
                    for item in stage_progress_details_raw:
                        if not isinstance(item, dict):
                            continue
                        if item.get("stage_name"):
                            stage_progress_details.append(
                                {
                                    "stage_name": str(item.get("stage_name", "") or ""),
                                    "companies_count": str(item.get("companies_count", 0) or 0),
                                    "opportunities_count": str(item.get("opportunities_count", 0) or 0),
                                }
                            )
                # 只保留有效的阶段推进记录；若无有效记录，则保持空数组

                # sales_process_evaluation
                spe = raw.get("sales_process_evaluation") if isinstance(raw.get("sales_process_evaluation"), dict) else {}
                task_stats = spe.get("task_statistics") if isinstance(spe.get("task_statistics"), dict) else {}
                task_summary = task_stats.get("summary") if isinstance(task_stats.get("summary"), dict) else {}
                overdue = task_stats.get("overdue_tasks") if isinstance(task_stats.get("overdue_tasks"), dict) else {}
                process_eval = spe.get("process_evaluation") if isinstance(spe.get("process_evaluation"), dict) else {}
                sales_quadrants = process_eval.get("sales_quadrants") if isinstance(process_eval.get("sales_quadrants"), dict) else {}

                resolved_department = department or ""

                return {
                    "performance_overview": [
                        {
                            "achievement_amount": _fmt_amount_yuan_to_wanyuan(quarterly.get("achievement_amount", 0.0)),
                            "completion_rate": _fmt_rate(quarterly.get("completion_rate", 0.0)),
                            "new_closed_amount": _fmt_amount_yuan_to_wanyuan(weekly_summary.get("new_closed_amount", 0.0)),
                            "customers_count": str(weekly_summary.get("customers_count", 0) or 0),
                            "opportunities_count": str(weekly_summary.get("opportunities_count", 0) or 0),
                            # deals_formatted 是已经处理好的字符串，可以直接使用
                            "deals_formatted": deals_formatted,
                        }
                    ],
                    "performance_forecast": [
                        {
                            "forecast_insight": str(perf_forecast.get("forecast_insight", "") or ""),
                            "closed_amount": _fmt_amount_yuan_to_wanyuan(closed.get("amount", 0.0)),
                            "closed_weekly_change": _fmt_amount_change_yuan_to_wanyuan(closed.get("weekly_change", 0.0), closed.get("weekly_change_direction")),
                            "commit_amount": _fmt_amount_yuan_to_wanyuan(commit.get("amount", 0.0)),
                            "commit_weekly_change": _fmt_amount_change_yuan_to_wanyuan(commit.get("weekly_change", 0.0), commit.get("weekly_change_direction")),
                            "upside_amount": _fmt_amount_yuan_to_wanyuan(upside.get("amount", 0.0)),
                            "upside_weekly_change": _fmt_amount_change_yuan_to_wanyuan(upside.get("weekly_change", 0.0), upside.get("weekly_change_direction")),
                        }
                    ],
                    "opportunity_progress": [
                        {
                            "weekly_new_opportunities_count": str(stage_changes.get("weekly_new_opportunities_count", 0) or 0),
                            "total_opportunities_in_progress": str(stage_changes.get("total_opportunities_in_progress", 0) or 0),
                        }
                    ],
                    "stage_progress_details": stage_progress_details,
                    "sales_process_evaluation": [
                        {
                            "total_tasks": str(task_summary.get("total_tasks", 0) or 0),
                            "completed_tasks": str(task_summary.get("completed_tasks", 0) or 0),
                            "uncompleted_tasks": str(task_summary.get("uncompleted_tasks", 0) or 0),
                            "cancelled_tasks": str(task_summary.get("cancelled_tasks", 0) or 0),
                            "completion_rate": _fmt_rate(task_summary.get("completion_rate", 0.0)),
                            "completion_rate_change": _fmt_rate_change(
                                task_summary.get("completion_rate_change", 0.0),
                                task_summary.get("completion_rate_change_direction"),
                            ),
                            "overdue_tasks_count": str(overdue.get("count", 0) or 0),
                            "overdue_tasks_affected_customers_count": str(overdue.get("affected_customers_count", 0) or 0),
                            "overdue_tasks_overview": str(overdue.get("overview", "") or ""),
                        }
                    ],
                    "sales_quadrants": [
                        {
                            "behavior_hh": _join_names(sales_quadrants.get("behavior_hh")),
                            "behavior_hl": _join_names(sales_quadrants.get("behavior_hl")),
                            "behavior_lh": _join_names(sales_quadrants.get("behavior_lh")),
                            "behavior_ll": _join_names(sales_quadrants.get("behavior_ll")),
                        }
                    ],
                    "department_name": resolved_department,
                    "start_date": start_date,
                    "end_date": end_date,
                }

            def _build_empty_department_weekly_report(department: str) -> dict[str, Any]:
                """构造空周报（接口无数据/失败时兜底），用于仍然推送给部门负责人。"""
                return {
                    # 下面字段保持与模板变量结构一致，便于模板渲染
                    "performance_overview": [
                        {
                            "achievement_amount": _fmt_amount_yuan_to_wanyuan(0.0),
                            "completion_rate": _fmt_rate(0.0),
                            "new_closed_amount": _fmt_amount_yuan_to_wanyuan(0.0),
                            "customers_count": "0",
                            "opportunities_count": "0",
                            "deals_formatted": ""
                        }
                    ],
                    "performance_forecast": [
                        {
                            "forecast_insight": "",
                            "closed_amount": _fmt_amount_yuan_to_wanyuan(0.0),
                            "closed_weekly_change": _fmt_amount_change_yuan_to_wanyuan(0.0, "increase"),
                            "commit_amount": _fmt_amount_yuan_to_wanyuan(0.0),
                            "commit_weekly_change": _fmt_amount_change_yuan_to_wanyuan(0.0, "increase"),
                            "upside_amount": _fmt_amount_yuan_to_wanyuan(0.0),
                            "upside_weekly_change": _fmt_amount_change_yuan_to_wanyuan(0.0, "increase")
                        }
                    ],
                    "opportunity_progress": [
                        {
                            "weekly_new_opportunities_count": "0",
                            "total_opportunities_in_progress": "0"
                        }
                    ],
                    "stage_progress_details": [],
                    "sales_process_evaluation": [
                        {
                            "total_tasks": "0",
                            "completed_tasks": "0",
                            "uncompleted_tasks": "0",
                            "cancelled_tasks": "0",
                            "completion_rate": _fmt_rate(0.0),
                            "completion_rate_change": _fmt_rate_change(0.0, "increase"),
                            "overdue_tasks_count": "0",
                            "overdue_tasks_affected_customers_count": "0",
                            "overdue_tasks_overview": ""
                        }
                    ],
                    "sales_quadrants": [
                        {
                            "behavior_hh": "",
                            "behavior_hl": "",
                            "behavior_lh": "",
                            "behavior_ll": ""
                        }
                    ],
                    # 推送/模板常用的顶层兜底字段
                    "department_name": department,
                    "start_date": start_date,
                    "end_date": end_date,
                }

            # 1) 部门 & 负责人：参照日报逻辑，优先从 OAuth 服务获取（可覆盖“无数据部门也推送”）
            departments_with_managers = oauth_client.get_departments_with_leaders()

            # OAuth 异常兜底：回退到本地 profile 的部门负责人集合（至少不影响线上推送）
            if not departments_with_managers:
                from app.repositories.user_profile import user_profile_repo
                dept_managers = user_profile_repo.get_all_departments_with_managers(session)
                departments_with_managers = {}
                for dept_name, manager in dept_managers.items():
                    if dept_name and manager is not None:
                        open_id = getattr(manager.oauth_user, "open_id", None)
                        platform = getattr(manager.oauth_user, "provider", None)
                        if open_id and platform:
                            departments_with_managers[dept_name] = [{
                                "open_id": open_id,
                                "name": manager.name or manager.direct_manager_name or "",
                                "type": "department_manager",
                                "department": dept_name,
                                "receive_id_type": "open_id",
                                "platform": platform,
                            }]

            # 预取周跟进总结的 summary_content（避免每个部门单独查库）
            dept_names = list(departments_with_managers.keys())
            dept_weekly_followup_summary_by_dept: dict[str, str] = {}
            if dept_names:
                dept_summaries = session.exec(
                    select(CRMWeeklyFollowupSummary).where(
                        CRMWeeklyFollowupSummary.week_start == start_date,
                        CRMWeeklyFollowupSummary.week_end == end_date,
                        CRMWeeklyFollowupSummary.summary_type == "department",
                        CRMWeeklyFollowupSummary.department_name.in_(dept_names),
                    )
                ).all()
                dept_weekly_followup_summary_by_dept = {
                    (s.department_name or ""): (s.summary_content or "") for s in dept_summaries
                }

            company_weekly_followup_summary: str = ""
            company_summary = session.exec(
                select(CRMWeeklyFollowupSummary).where(
                    CRMWeeklyFollowupSummary.week_start == start_date,
                    CRMWeeklyFollowupSummary.week_end == end_date,
                    CRMWeeklyFollowupSummary.summary_type == "company",
                    CRMWeeklyFollowupSummary.department_name == "",
                )
            ).first()
            if company_summary:
                company_weekly_followup_summary = company_summary.summary_content or ""

            # 2) 逐部门获取周报：即使接口失败/无数据，也要推送空周报
            if not report_type or report_type == "department":
                for department_name, managers in departments_with_managers.items():
                    if not managers:
                        continue
                    try:
                        dept_report = aldebaran_client.fetch_weekly_report(
                            report_year=report_year,
                            report_week_of_year=report_week_of_year,
                            department_name=department_name,
                        )
                    except Exception as e:
                        logger.warning(f"获取部门周报失败，改为推送空周报: department={department_name}, err={e}")
                        dept_report = _build_empty_department_weekly_report(department_name)
                    else:
                        # 接口返回有数据时，重组结构以适配模板
                        dept_report = _rebuild_weekly_report_for_template(dept_report, department_name)

                    # 补齐卡片模板用的超链接变量（URL）
                    report_info_1 = crm_statistics_service._get_weekly_report_info(session, "review1s", end_date, department_name)
                    report_info_5 = crm_statistics_service._get_weekly_report_info(session, "review5", end_date, department_name)
                    dept_report["weekly_review_1_page"] = (
                        crm_statistics_service._get_weekly_report_url(report_info_1["execution_id"], "review1s")
                        if report_info_1 and report_info_1.get("execution_id")
                        else f"{settings.REVIEW_REPORT_HOST}"
                    )
                    dept_report["weekly_review_5_page"] = (
                        crm_statistics_service._get_weekly_report_url(report_info_5["execution_id"], "review5")
                        if report_info_5 and report_info_5.get("execution_id")
                        else f"{settings.REVIEW_REPORT_HOST}"
                    )
                    dept_report["weekly_tasks_page"] = (
                        f"{settings.CRM_SALES_TASK_PAGE_URL}"
                    )
                    dept_report["weekly_followup_page"] = (
                        f"{settings.REVIEW_REPORT_HOST}/review/opportunitySummary"
                        f"?department_name={quote_plus(department_name)}"
                        f"&week_start={start_date.isoformat()}&week_end={end_date.isoformat()}"
                    )
                    dept_report["weekly_followup_summary"] = dept_weekly_followup_summary_by_dept.get(department_name, "")

                    department_reports.append(dept_report)

            # 3) 公司周报：仍按接口获取（若失败则跳过公司推送）
            if not report_type or report_type == "company":
                try:
                    company_weekly_report = aldebaran_client.fetch_weekly_report(
                        report_year=report_year,
                        report_week_of_year=report_week_of_year,
                        department_name=None,
                    )
                    company_weekly_report = _rebuild_weekly_report_for_template(company_weekly_report, None)

                    report_info_1 = crm_statistics_service._get_weekly_report_info(session, "review1", end_date, None)
                    report_info_5 = crm_statistics_service._get_weekly_report_info(session, "review5", end_date, None)
                    company_weekly_report["weekly_review_1_page"] = (
                        crm_statistics_service._get_weekly_report_url(report_info_1["execution_id"], "review1")
                        if report_info_1 and report_info_1.get("execution_id")
                        else f"{settings.REVIEW_REPORT_HOST}"
                    )
                    company_weekly_report["weekly_review_5_page"] = (
                        crm_statistics_service._get_weekly_report_url(report_info_5["execution_id"], "review5")
                        if report_info_5 and report_info_5.get("execution_id")
                        else f"{settings.REVIEW_REPORT_HOST}"
                    )
                    company_weekly_report["weekly_tasks_page"] = (
                        f"{settings.CRM_SALES_TASK_PAGE_URL}"
                    )
                    company_weekly_report["weekly_followup_page"] = (
                        f"{settings.REVIEW_REPORT_HOST}/review/opportunitySummary"
                        f"?week_start={start_date.isoformat()}&week_end={end_date.isoformat()}"
                    )
                    company_weekly_report["weekly_followup_summary"] = company_weekly_followup_summary
                except Exception as e:
                    logger.error(f"获取公司周报失败: err={e}")

            if not department_reports and not company_weekly_report:
                logger.warning(
                    "未获取到任何周报数据（也没有可推送部门负责人）: %s 到 %s (report_year=%s, report_week=%s)",
                    start_date,
                    end_date,
                    report_year,
                    report_week_of_year,
                )
                return {"success": False, "message": "未获取到任何部门负责人或周报数据", "data": {}}

            logger.info(
                "周报接口数据获取完成: 部门周报=%s 个，公司周报=%s",
                len(department_reports),
                bool(company_weekly_report),
            )
            company_report_generated = bool(company_weekly_report)
            
            # 如果启用了飞书推送，发送周报通知
            if settings.CRM_WEEKLY_REPORT_FEISHU_ENABLED:
                department_success_count = 0
                department_failed_count = 0
                company_success = False
                
                if not report_type or report_type == 'department':
                    # 发送部门周报通知
                    for department_report in department_reports:
                        try:
                            # recipients：优先使用 OAuth 返回的负责人列表（无数据部门也能推送）
                            dept_name = department_report.get("department_name")
                            recipients = departments_with_managers.get(dept_name) if dept_name else None
                            # 发送部门周报通知给部门负责人
                            result = platform_notification_service.send_weekly_report_notification(
                                db_session=session,
                                department_report_data=department_report,
                                recipients=recipients,
                            )
                            
                            if result["success"]:
                                department_success_count += 1
                                logger.info(
                                    f"成功发送 {department_report.get('department_name', '未知部门')} 周报通知，"
                                    f"推送成功 {result['success_count']}/{result['recipients_count']} 次"
                                )
                            else:
                                department_failed_count += 1
                                logger.warning(f"部门周报通知发送失败: {result['message']}")
                                
                        except Exception as e:
                            department_failed_count += 1
                            logger.error(f"发送部门周报通知时出错: {str(e)}")
                
                if not report_type or report_type == 'company':
                    # 发送公司周报通知
                    if company_weekly_report:
                        try:
                            company_result = platform_notification_service.send_company_weekly_report_notification(
                                db_session=session,
                                company_weekly_report_data=company_weekly_report
                            )
                            
                            if company_result["success"]:
                                company_success = True
                                logger.info(
                                    f"成功发送公司周报通知，"
                                    f"推送成功 {company_result['success_count']}/{company_result['recipients_count']} 次"
                                )
                            else:
                                logger.warning(f"公司周报通知发送失败: {company_result['message']}")
                                
                        except Exception as e:
                            logger.error(f"发送公司周报通知时出错: {str(e)}")
                    else:
                        logger.warning(f"{start_date} 到 {end_date} 没有找到任何公司周报数据，跳过公司周报推送")
                
                logger.info(f"CRM周报飞书通知发送完成: 部门周报成功 {department_success_count} 个，失败 {department_failed_count} 个，公司周报推送{'成功' if company_success else '失败'}")
                
                return {
                    "success": True,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "department_count": len(department_reports),
                    "company_report_generated": company_report_generated,
                    "report_year": report_year,
                    "report_week_of_year": report_week_of_year,
                    "feishu_enabled": True,
                    "department_success_count": department_success_count,
                    "department_failed_count": department_failed_count,
                    "company_notification_success": company_success,
                    "message": f"成功处理了 {len(department_reports)} 个部门的周报数据，{'生成并推送' if company_success else '生成'}公司周报，飞书推送: 部门周报成功 {department_success_count} 个，失败 {department_failed_count} 个"
                }
            else:
                logger.info("飞书推送已禁用，仅生成周报数据")
                return {
                    "success": True,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "department_count": len(department_reports),
                    "company_report_generated": company_report_generated,
                    "report_year": report_year,
                    "report_week_of_year": report_week_of_year,
                    "feishu_enabled": False,
                    "message": f"成功处理了 {len(department_reports)} 个部门的周报数据，{'生成' if company_report_generated else '未生成'}公司周报数据，飞书推送已禁用"
                }
                
    except Exception as e:
        logger.exception(f"CRM周报数据生成任务执行失败: {e}")
        # 使用Celery的重试机制
        self.retry(exc=e, countdown=300)  # 5分钟后重试


@app.task(bind=True, max_retries=3)
def generate_crm_weekly_followup_summary(self, start_date_str=None, end_date_str=None):
    """
    生成“周跟进总结”（公司/团队整体描述 + 明细列表），用于后台页面展示与人工评论。
    周区间口径：周日到周六，与现有周报一致。

    Args:
        start_date_str: 开始日期 YYYY-MM-DD，不传默认上周日
        end_date_str: 结束日期 YYYY-MM-DD，不传默认本周六
    """
    try:
        # 计算日期范围
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                logger.info(f"开始执行CRM周跟进总结生成任务，日期范围: {start_date} 到 {end_date}")
            except ValueError:
                logger.error(f"无效的日期格式: start_date={start_date_str}, end_date={end_date_str}")
                return {"success": False, "message": "无效的日期格式", "data": {}}
        else:
            today = beijing_today_date()
            days_since_sunday = (today.weekday() + 1) % 7
            last_sunday = today - timedelta(days=days_since_sunday + 7)
            this_saturday = last_sunday + timedelta(days=6)
            start_date = last_sunday
            end_date = this_saturday
            logger.info(f"开始执行CRM周跟进总结生成任务，默认处理上周日到本周六: {start_date} 到 {end_date}")

        with Session(engine) as session:
            result = crm_weekly_followup_service.generate_weekly_followup(
                session=session,
                week_start=start_date,
                week_end=end_date,
            )
            return {"success": True, "message": "ok", "data": result}

    except Exception as e:
        logger.exception(f"CRM周跟进总结生成任务执行失败: {e}")
        self.retry(exc=e, countdown=300)


@app.task(bind=True)
def crm_visit_records_writeback(self, start_date_str=None, end_date_str=None, writeback_mode=None):
    """
    CRM销售拜访记录数据回写任务
    根据配置的频率执行：weekly（每周日下午2点，处理上周日到本周六）或daily（每天执行，处理昨天）
    
    Args:
        start_date_str: 开始日期字符串，格式YYYY-MM-DD，不传则根据频率配置自动计算
        end_date_str: 结束日期字符串，格式YYYY-MM-DD，不传则根据频率配置自动计算
        writeback_mode: 回写模式，不传则使用配置中的默认值
    
    工作流程：
    1. 根据配置的频率计算日期范围：
       - weekly：计算上周日到本周六的日期范围
       - daily：计算昨天的日期范围
    2. 从crm_sales_visit_records表查询该时间范围内的拜访记录
    3. 根据回写模式选择处理方式：
       - CBG模式：为每条拜访记录创建纷享销客的日常对象
       - APAC模式：为每条拜访记录创建Salesforce的任务
       - OLM模式：为每条拜访记录创建销售易的拜访记录
       - CHAITIN模式：为每条拜访记录创建长亭的拜访记录
    4. 调用相应的API进行回写或任务创建，并返回回写结果
    """
    try:
        # 如果没有指定回写模式，使用配置中的默认值
        if writeback_mode is None:
            writeback_mode = settings.CRM_WRITEBACK_DEFAULT_MODE.value
        
        # 验证回写模式
        valid_modes = [mode.value for mode in WritebackMode]
        if writeback_mode not in valid_modes:
            logger.error(f"无效的回写模式: {writeback_mode}，支持的模式: {valid_modes}")
            return {
                "success": False,
                "message": f"无效的回写模式: {writeback_mode}，支持的模式: {valid_modes}",
                "data": {}
            }
        
        # 计算日期范围
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                logger.info(f"开始执行CRM拜访记录回写任务，指定日期范围: {start_date} 到 {end_date}")
            except ValueError:
                logger.error(f"无效的日期格式: start_date={start_date_str}, end_date={end_date_str}")
                return {
                    "success": False,
                    "message": "无效的日期格式",
                    "data": {}
                }
        else:
            # 根据配置的频率自动计算日期范围
            # 使用配置的时区获取当前日期，确保时区一致性
            tz = pytz.timezone(settings.CRM_WRITEBACK_TIMEZONE)
            today = datetime.now(tz).date()
            frequency = settings.CRM_WRITEBACK_FREQUENCY
            
            if frequency == WritebackFrequency.DAILY:
                # 按天回写：处理昨天的数据
                start_date = today - timedelta(days=1)
                end_date = start_date
                logger.info(f"开始执行CRM拜访记录回写任务，按天模式，处理昨天: {start_date} (时区: {settings.CRM_WRITEBACK_TIMEZONE})")
            else:  # WritebackFrequency.WEEKLY
                # 按周回写：处理上周日到本周六的数据
                # 计算上周日（今天往前推7天，然后找到最近的周日）
                days_since_sunday = (today.weekday() + 1) % 7  # 0=周一，1=周二，...，6=周日
                last_sunday = today - timedelta(days=days_since_sunday + 7)
                this_saturday = last_sunday + timedelta(days=6)
                
                start_date = last_sunday
                end_date = this_saturday
                logger.info(f"开始执行CRM拜访记录回写任务，按周模式，处理上周日到本周六: {start_date} 到 {end_date} (时区: {settings.CRM_WRITEBACK_TIMEZONE})")
        
        # 将本地时区的日期范围转换为UTC时间（数据库中last_modified_time是UTC时间）
        writeback_tz = pytz.timezone(settings.CRM_WRITEBACK_TIMEZONE)
        # 构建本地时区的开始和结束时间
        start_local = datetime.combine(start_date, datetime.min.time())
        end_local = datetime.combine(end_date, datetime.max.time())
        # 添加时区信息并转换为UTC
        start_local = writeback_tz.localize(start_local)
        end_local = writeback_tz.localize(end_local)
        start_dt_utc = start_local.astimezone(pytz.UTC)
        end_dt_utc = end_local.astimezone(pytz.UTC)
        # 移除时区信息（数据库中的datetime字段通常以naive UTC存储）
        start_datetime = start_dt_utc.replace(tzinfo=None)
        end_datetime = end_dt_utc.replace(tzinfo=None)
        
        logger.info(f"转换为UTC时间范围: {start_datetime} 到 {end_datetime} (UTC)")
        
        with Session(engine) as session:
            # 执行拜访记录回写
            result = crm_writeback_service.writeback_visit_records(
                session=session,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                writeback_mode=writeback_mode
            )
            
            if result["success"]:
                logger.info(f"CRM拜访记录回写任务执行成功: {result['message']}")
                return_data = {
                    "success": True,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "processed_count": result.get("processed_count", 0),
                    "writeback_mode": writeback_mode,
                    "message": result["message"]
                }
                
                # 添加回写的统计信息
                return_data["writeback_count"] = result.get("writeback_count", 0)
                return_data["success_count"] = result.get("success_count", 0)
                return_data["failed_count"] = result.get("failed_count", 0)
                
                return return_data
            else:
                logger.error(f"CRM拜访记录回写任务执行失败: {result['message']}")
                return {
                    "success": False,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "message": result["message"]
                }
                
    except Exception as e:
        logger.exception(f"CRM拜访记录回写任务执行失败: {e}")
        return {
            "success": False,
            "message": f"任务执行失败: {str(e)}",
            "data": {}
        }

@app.task(bind=True)
def send_sales_task_summary(self, start_date_str=None, end_date_str=None):
    """
    销售任务总结推送任务
    从crm_todos表获取数据，按负责人推送销售任务卡片（飞书/钉钉）。
    
    Args:
        start_date_str: 开始日期字符串，格式YYYY-MM-DD，不传则默认为上周日
        end_date_str: 结束日期字符串，格式YYYY-MM-DD，不传则默认为本周六
    """
    try:
        logger.info("开始执行销售任务总结任务（数据源：crm_todos）")

        # 计算上周的日期范围
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                logger.info(f"开始执行销售任务总结任务，日期范围: {start_date} 到 {end_date}")
            except ValueError:
                logger.error(f"无效的日期格式: start_date={start_date_str}, end_date={end_date_str}")
                return {
                    "success": False,
                    "message": "无效的日期格式",
                    "data": {}
                }
        else:
            # 默认处理上周日到本周六的数据
            today = beijing_today_date()
            # 计算上周日（今天往前推7天，然后找到最近的周日）
            days_since_sunday = (today.weekday() + 1) % 7  # 0=周一，1=周二，...，6=周日
            last_sunday = today - timedelta(days=days_since_sunday + 7)
            this_saturday = last_sunday + timedelta(days=6)
            
            start_date = last_sunday
            end_date = this_saturday
            logger.info(f"开始执行销售任务总结任务，默认处理上周日到本周六: {start_date} 到 {end_date}")
        
        with Session(engine) as session:            
            # 读取crm_todos中需要推送的任务（按时间范围过滤，只查询due_date不为空的记录）
            # 本周已完成的任务(任务状态为COMPLETED)
            this_week_sales_tasks = _get_sales_tasks_from_crm_todos(session, start_date, end_date, status_list=["COMPLETED"])
            
            # 本周已取消的任务(任务状态为CANCELLED)
            cancelled_by_assignee_count = _count_cancelled_tasks(session, start_date, end_date)
            total_cancelled_count = sum(cancelled_by_assignee_count.values())
            
            # 下周待完成(任务状态为PENDING或IN_PROGRESS)
            next_week_start_date = start_date + timedelta(days=7)
            next_week_end_date = end_date + timedelta(days=7)
            next_week_sales_tasks = _get_sales_tasks_from_crm_todos(session, next_week_start_date, next_week_end_date, status_list=["PENDING", "IN_PROGRESS"])
            
            # 截止到本周的所有未完成任务(任务状态为OVERDUE)
            overdue_tasks = _get_sales_tasks_from_crm_todos(session, range_start=None, range_end=end_date, status_list=["PENDING", "IN_PROGRESS"])
            
            # 单独统计due_date为空的记录数量（与时间范围无关，只需要数量）
            no_due_date_count = _count_no_due_date_tasks(session)
            total_no_due_date = sum(no_due_date_count.values())
            
            logger.info(f"crm_todos 读取到本周已完成 {len(this_week_sales_tasks)} 条，下周待完成 {len(next_week_sales_tasks)} 条，截止到本周的全部逾期任务 {len(overdue_tasks)} 条，本周已取消 {total_cancelled_count} 条(涉及 {len(cancelled_by_assignee_count)} 个负责人)，due_date为空 {total_no_due_date} 条（涉及 {len(no_due_date_count)} 个负责人）")
            
            # 按负责人统计任务（本周和下周的任务都有due_date，due_date为空的单独统计）
            analyze_results = _analyze_crm_todos(
                this_week_sales_tasks,
                next_week_sales_tasks,
                overdue_tasks,
                start_date,
                end_date,
                next_week_start_date,
                next_week_end_date,
                no_due_date_count,
                cancelled_by_assignee_count,
            )
            # 即使没有任何任务数据，也要推送“0任务”卡片（但只推送给统计周期内有 todo 的负责人）
            if not analyze_results:
                logger.info("crm_todos 本周无任务数据，构造 0 指标卡片用于推送")
                # 兜底来源：本周已取消 + due_date 为空（这两类在无 due_date 任务时可能导致 analyze_results 为空）
                assignee_keys = set(cancelled_by_assignee_count.keys()) | set(no_due_date_count.keys())
                if not assignee_keys:
                    logger.info("统计周期内没有任何 todo 负责人（含取消/无截止日期），无需推送 0 指标卡片")
                else:
                    start_date_str2 = start_date.isoformat()
                    end_date_str2 = end_date.isoformat()
                    next_week_start_date_str2 = next_week_start_date.isoformat()
                    next_week_end_date_str2 = next_week_end_date.isoformat()

                    analyze_results = []
                    for assignee_key in assignee_keys:
                        cancelled_cnt = int(cancelled_by_assignee_count.get(assignee_key, 0))
                        no_due_cnt = int(no_due_date_count.get(assignee_key, 0))

                        # assignee_key 可能是人名，也可能是 owner_id；两者都传入，push 侧会按 id 优先、name 兜底匹配
                        assignee_name = assignee_key
                        assignee_id = assignee_key

                        statistics_item = {
                            "total_completed": "0",
                            "total_new_created": "0",
                            "total_due_tasks": "0",
                            "total_cancelled": str(cancelled_cnt),
                            "due_src1": "0",
                            "due_src2": "0",
                            "due_src3": "0",
                            "next_src1": "0",
                            "next_src2": "0",
                            "next_src3": "0",
                            "completed_src1": "0",
                            "completed_src2": "0",
                            "completed_src3": "0",
                            "others": str(no_due_cnt),
                            "cancelled_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_name}&due_date__gte={start_date_str2}&due_date__lte={end_date_str2}&ai_status=CANCELLED",
                        }

                        analyze_results.append(
                            {
                                "start_date": start_date_str2,
                                "end_date": end_date_str2,
                                "assignee_name": assignee_name,
                                "assignee_id": assignee_id,
                                "statistics": [statistics_item],
                                "due_task_list": [],
                                "next_week_task_list": [],
                                "completed_by_source": {},
                                "overdue_by_source": {},
                                "next_week_by_source": {},
                                "cancelled_count": cancelled_cnt,
                                "no_due_date_count": no_due_cnt,
                                "due_task_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_name}&due_date__lte={end_date_str2}&is_overdue=True",
                                "next_week_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_name}&due_date__gte={next_week_start_date_str2}&due_date__lte={next_week_end_date_str2}&ai_status=PENDING&ai_status=IN_PROGRESS",
                            }
                        )
            logger.info(f"分析结果: {len(analyze_results)} 个负责人的任务统计")

            # 将个人周统计落库，供后续“按部门/公司汇总”接口查询
            # 注意：若目标表尚未建好，不应影响原有推送主流程
            try:
                persisted_rows = crm_sales_task_statistics_service.persist_weekly_user_metrics(
                    session=session,
                    analyze_results=analyze_results,
                    week_start=start_date,
                    week_end=end_date,
                )
                logger.info(f"已写入销售任务周指标 {persisted_rows} 行（用于部门/公司汇总）")
            except Exception as e:
                logger.exception(f"写入销售任务周指标失败（不影响推送主流程）: {e}")
            success_count = 0
            failed_count = 0
            failed_tasks = []

            for task_data in analyze_results:
                try:
                    result = platform_notification_service.send_sales_task_notification(
                        db_session=session,
                        task_data=task_data
                    )
                    if result.get("success"):
                        success_count += 1
                        logger.info(
                            f"成功推送销售任务给 {task_data.get('assignee_name')}，"
                            f"推送成功 {result['success_count']}/{result['recipients_count']} 次"
                        )
                    else:
                        failed_count += 1
                        failed_tasks.append({
                            "assignee_name": task_data.get("assignee_name"),
                            "error": result.get("message", "未知错误")
                        })
                        logger.warning(f"销售任务推送失败: {result.get('message')}")
                except Exception as e:
                    failed_count += 1
                    failed_tasks.append({
                        "assignee_name": task_data.get("assignee_name"),
                        "error": str(e)
                    })
                    logger.exception(f"推送销售任务时异常: {e}")

            logger.info(
                f"销售任务总结任务完成: 总数 {len(analyze_results)}，成功 {success_count}，失败 {failed_count}"
            )

            return {
                "success": True,
                "message": f"销售任务总结完成: 成功 {success_count} 个，失败 {failed_count} 个",
                "processed_count": len(analyze_results),
                "success_count": success_count,
                "failed_count": failed_count,
                "failed_tasks": failed_tasks,
                "analysis_results": analyze_results
            }
    except Exception as e:
        logger.exception(f"销售任务总结任务执行失败: {e}")
        self.retry(exc=e, countdown=300)


def _get_sales_tasks_from_crm_todos(
    session: Session,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    status_list: list[str] = [],
) -> list[dict]:
    """
    从crm_todos表读取符合指定时间范围的销售任务，返回统一的task_data结构。

    查询条件：
    - 负责人存在（owner_name 或 owner_id）
    - due_date 在指定的时间范围内（range_start 到 range_end）
    
    参数:
        session: 数据库会话
        range_start: 时间范围起始时间（包含）
        range_end: 时间范围结束时间（包含）
        status_list: 任务状态列表
    返回:
        符合时间范围的销售任务列表
    """
    from sqlalchemy import text

    # 构建时间范围查询条件
    sql_params = {}
    time_conditions = []
    
    if range_start is not None:
        time_conditions.append("due_date >= :range_start")
        sql_params["range_start"] = range_start
    
    if range_end is not None:
        time_conditions.append("due_date <= :range_end")
        sql_params["range_end"] = range_end
    
    if time_conditions:
        time_condition = " AND ".join(time_conditions)
        # 如果有时间条件，添加 AND 前缀
        time_condition_clause = f"AND {time_condition}"
    else:
        # 如果起止时间都为None，则不添加时间条件（查询所有记录）
        time_condition_clause = ""
    
    # 构建状态列表查询条件
    if status_list and len(status_list) > 0:
        # 为每个状态创建占位符
        status_placeholders = ", ".join([f":status_{i}" for i in range(len(status_list))])
        status_condition = f"AND ai_status IN ({status_placeholders})"
        # 添加状态参数
        for i, status in enumerate(status_list):
            sql_params[f"status_{i}"] = status
    else:
        status_condition = ""
    
    sql = text(
        f"""
        SELECT
            title,
            due_date,
            ai_status,
            owner_id,
            owner_name,
            opportunity_id,
            opportunity_name,
            account_id,
            account_name,
            data_source
        FROM crm_todos
        WHERE (owner_name IS NOT NULL OR owner_id IS NOT NULL)
            AND data_source IS NOT NULL
            AND due_date IS NOT NULL
            {time_condition_clause}
            {status_condition}
        ORDER BY due_date ASC
        """
    )
    rows = session.exec(sql, params=sql_params).fetchall()
 
    tasks: list[dict] = []
    for r in rows:
        # 将Row映射为dict（sqlmodel返回Row）
        row = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        tasks.append({
            "assignee_name": row.get("owner_name"),
            "assignee_id": row.get("owner_id"),
            "task_name": row.get("title"),
            "due_date": row.get("due_date").isoformat(),
            "ai_status": row.get("ai_status"),
            "account_id": row.get("account_id"),
            "account_name": row.get("account_name"),
            "opportunity_id": row.get("opportunity_id"),
            "opportunity_name": row.get("opportunity_name"),
            "data_source": row.get("data_source"),
        })

    return tasks


def _get_assignee_key_and_name_from_data(owner_name: str | None, owner_id: str | None) -> tuple[str | None, str | None]:
    """
    获取负责人标识和名称（公共方法，处理JSON格式的assignee_name）
    
    owner_name: 负责人名称
    owner_id: 负责人ID
    
    返回:
        (assignee_key, assignee_name)
        - assignee_key: 用于分组的标识
        - assignee_name: 解析后的显示名称（如果是JSON则提取实际人名）
    """
        
    # assignee_name 解析JSON格式（用于显示）
    assignee_name = owner_name
    if assignee_name:
        try:
            import json
            parsed = json.loads(assignee_name)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], str):
                assignee_name = parsed[0]
            elif isinstance(parsed, dict):
                v = parsed.get("name")
                if isinstance(v, str) and v.strip():
                    assignee_name = v
            elif isinstance(parsed, str) and parsed.strip():
                assignee_name = parsed
        except Exception:
            # 非 JSON 字符串，保持原值
            pass
    
    assignee_key = assignee_name or owner_name or owner_id
    return assignee_key, assignee_name


def _count_cancelled_tasks(session: Session, start_date: datetime, end_date: datetime) -> dict[str, int]:
    """
    统计crm_todos表中已取消的销售任务数量，按负责人分组。
    
    查询条件：
    - 负责人存在（owner_name 或 owner_id）
    - ai_status为CANCELLED
    - due_date在指定的时间范围内（start_date 到 end_date）
    返回:
        按负责人分组的已取消任务数量字典，key为负责人标识（与_analyze_crm_todos中的处理逻辑一致）
    """
    from sqlalchemy import text
    
    # 先查询出所有记录，然后在Python中处理assignee_key，确保与_analyze_crm_todos中的逻辑一致
    sql = text(
        """
        SELECT
            owner_name,
            owner_id
        FROM crm_todos
        WHERE (owner_name IS NOT NULL OR owner_id IS NOT NULL)
            AND data_source IS NOT NULL
            AND ai_status = 'CANCELLED'
            AND due_date >= :start_date
            AND due_date <= :end_date
        """
    )
    rows = session.exec(sql, params={"start_date": start_date, "end_date": end_date}).fetchall()
    
    # 使用公共方法处理assignee_key
    cancelled_count = {}
    for r in rows:
        assignee_key, _ = _get_assignee_key_and_name_from_data(r.owner_name, r.owner_id)
        if assignee_key:
            cancelled_count[assignee_key] = cancelled_count.get(assignee_key, 0) + 1

    return cancelled_count


def _count_no_due_date_tasks(session: Session) -> dict[str, int]:
    """
    统计crm_todos表中due_date为空的销售任务数量，按负责人分组。
    
    查询条件：
    - 负责人存在（owner_name 或 owner_id）
    - due_date IS NULL
    - ai_status为PENDING或IN_PROGRESS
    
    返回:
        按负责人分组的due_date为空的任务数量字典，key为负责人标识（与_analyze_crm_todos中的处理逻辑一致）
    """
    from sqlalchemy import text
    
    # 先查询出所有记录，然后在Python中处理assignee_key，确保与_analyze_crm_todos中的逻辑一致
    sql = text(
        """
        SELECT
            owner_name,
            owner_id
        FROM crm_todos
        WHERE (owner_name IS NOT NULL OR owner_id IS NOT NULL)
            AND data_source IS NOT NULL
            AND ai_status IN ('PENDING', 'IN_PROGRESS')
            AND due_date IS NULL
        """
    )
    rows = session.exec(sql).fetchall()
    
    # 使用公共方法处理assignee_key
    no_due_date_count = {}
    for r in rows:
        assignee_key, _ = _get_assignee_key_and_name_from_data(r.owner_name, r.owner_id)
        if assignee_key:
            no_due_date_count[assignee_key] = no_due_date_count.get(assignee_key, 0) + 1

    return no_due_date_count


def _analyze_crm_todos(
    this_week_sales_tasks: list[dict],
    next_week_sales_tasks: list[dict],
    overdue_tasks: list[dict],
    start_date: datetime,
    end_date: datetime,
    next_week_start_date: datetime,
    next_week_end_date: datetime,
    no_due_date_count: dict[str, int] | None = None,
    total_cancelled_count: dict[str, int] | None = None
) -> list[dict]:
    """
    对本周和下周的销售任务进行状态数量统计，按负责人（assignee）分组。
    
    统计内容：
    1) 已完成数量（按 data_source 分组统计）- 来自本周已完成任务
    2) 已逾期数量（按 data_source 分组统计）- 截止到本周的全部逾期任务
    3) 下周待完成数量（按 data_source 分组统计）- 来自下周未完成任务
    4) 已取消数量 - 来自本周已取消任务
    5) due_date为空数量 - 来自全部due_date为空的任务

    参数:
        this_week_sales_tasks: 本周已完成的任务列表（已按时间范围筛选）
        next_week_sales_tasks: 下周待完成任务列表（已按时间范围筛选）
        overdue_tasks: 截止到本周的全部逾期任务列表（已按时间范围筛选）
        start_date: 开始日期
        end_date: 结束日期
        next_week_start_date: 下周开始日期
        next_week_end_date: 下周结束日期
        no_due_date_count: due_date为空的任务数量
        total_cancelled_count: 本周已取消的任务数量
    返回:
        按负责人分组的统计结果列表，每个元素包含：
        - start_date, end_date, assignee_name
        - statistics: 统计信息数组（包含各种计数）
        - due_task_list: 本周未完成任务明细
        - next_week_task_list: 下周未完成任务明细

    注：
    - "完成" 判定：本周内ai_status为COMPLETED的任务
    - "逾期/未完成" 判定：截止到本周ai_status为PENDING或IN_PROGRESS的任务
    - "取消" 判定：本周内ai_status为CANCELLED的任务
    - "下周待完成" 判定：下周ai_status为PENDING或IN_PROGRESS的任务
    """
    from datetime import datetime
    
    # 按负责人分组
    by_assignee: dict[str, dict] = {}
    
    def get_data_source_display_name(data_source: str | None) -> str:
        """获取数据源的显示名称"""
        if not data_source:
            return "Unknown"
        try:
            # 尝试将字符串转换为枚举
            source_type = TodoDataSourceType(data_source)
            return source_type.display_name()
        except (ValueError, TypeError):
            # 如果不是有效的枚举值，返回原值
            return data_source
        
    def _build_task_detail(task: dict, due_date: str | None) -> dict:
        """构建任务详情"""
        return {
            "data_source": get_data_source_display_name(task.get("data_source")),
            "account_name": task.get("account_name"),
            "opportunity_name": task.get("opportunity_name"),
            "title": task.get("task_name"),
            "due_date": due_date,
        }

    # 处理本周任务：统计已完成和已取消
    for task in this_week_sales_tasks:
        assignee_key, assignee_name = _get_assignee_key_and_name_from_data(task.get("assignee_name"), task.get("assignee_id"))
        if assignee_key not in by_assignee:
            by_assignee[assignee_key] = {
                "assignee_name": assignee_name,
                "assignee_id": task.get("assignee_id"),
                "completed": [],
                "completed_by_source": {},
                "overdue": [],
                "overdue_by_source": {},
                "cancelled": [],
                "cancelled_by_source": {},
                "next_week": [],
                "next_week_by_source": {},
            }

        assignee_data = by_assignee[assignee_key]
        
        # 解析 due_date
        due_date = task.get("due_date")
        
        # 判断任务状态（处理 ai_status 可能为 None 的情况）
        status = (task.get("ai_status") or "Unknown").upper()
        is_completed = status == "COMPLETED"
        is_cancelled = status == "CANCELLED"
        
        # 构建任务详情
        task_detail = _build_task_detail(task, due_date)
        data_source = task.get("data_source") or "Unknown"
        
        # 分类统计本周任务（列表已按时间筛选，只需按状态统计）
        if is_completed:
            # 已完成任务
            assignee_data["completed"].append(task_detail)
            assignee_data["completed_by_source"][data_source] = assignee_data["completed_by_source"].get(data_source, 0) + 1
        elif not is_cancelled:
            # 未完成且未取消的任务 = 逾期任务（因为列表已按时间筛选）
            # 注意：这里处理的任务已经过滤掉了due_date为空的记录
            assignee_data["overdue"].append(task_detail)
            assignee_data["overdue_by_source"][data_source] = assignee_data["overdue_by_source"].get(data_source, 0) + 1
    
    
    # 处理截止到本周的全部逾期任务
    for task in overdue_tasks:
        assignee_key, assignee_name = _get_assignee_key_and_name_from_data(task.get("assignee_name"), task.get("assignee_id"))
        if assignee_key not in by_assignee:
            by_assignee[assignee_key] = {
                "assignee_name": assignee_name,
                "assignee_id": task.get("assignee_id"),
                "completed": [],
                "completed_by_source": {},
                "overdue": [],
                "overdue_by_source": {},
                "cancelled": [],
                "cancelled_by_source": {},
                "next_week": [],
                "next_week_by_source": {},
            }

        assignee_data = by_assignee[assignee_key]
        
        # 解析 due_date
        due_date = task.get("due_date")
                
        # 构建任务详情
        task_detail = _build_task_detail(task, due_date)
        data_source = task.get("data_source") or "Unknown"
        
        # 未完成且未取消的任务 = 逾期任务（因为列表已按时间筛选）
        # 注意：这里处理的任务已经过滤掉了due_date为空的记录
        assignee_data["overdue"].append(task_detail)
        assignee_data["overdue_by_source"][data_source] = assignee_data["overdue_by_source"].get(data_source, 0) + 1
    
    # 处理下周任务：统计下周待完成
    for task in next_week_sales_tasks:
        assignee_key, assignee_name = _get_assignee_key_and_name_from_data(task.get("assignee_name"), task.get("assignee_id"))
        assignee_id = task.get("assignee_id")
        
        if not assignee_key:
            continue  # 跳过没有负责人的任务
        
        if assignee_key not in by_assignee:
            by_assignee[assignee_key] = {
                "assignee_name": assignee_name,
                "assignee_id": assignee_id,
                "completed": [],
                "completed_by_source": {},
                "overdue": [],
                "overdue_by_source": {},
                "cancelled": [],
                "cancelled_by_source": {},
                "next_week": [],
                "next_week_by_source": {},
            }

        assignee_data = by_assignee[assignee_key]
        
        # 解析 due_date
        due_date = task.get("due_date")
        
        # 判断任务状态（处理 ai_status 可能为 None 的情况）
        status = (task.get("ai_status") or "Unknown").upper()
        is_completed = status == "COMPLETED"
        is_cancelled = status == "CANCELLED"
        
        # 构建任务详情
        task_detail = _build_task_detail(task, due_date)
        data_source = task.get("data_source") or "Unknown"
        
        # 统计下周待完成任务（未完成且未取消的任务）
        if not is_completed and not is_cancelled:
            assignee_data["next_week"].append(task_detail)
            assignee_data["next_week_by_source"][data_source] = assignee_data["next_week_by_source"].get(data_source, 0) + 1

    # 整理结果，按负责人分组，按照指定格式输出
    result_list = []
    
    # 格式化日期
    start_date_str = start_date.date().isoformat() if isinstance(start_date, datetime) else str(start_date)
    end_date_str = end_date.date().isoformat() if isinstance(end_date, datetime) else str(end_date)
    next_week_start_date_str = next_week_start_date.date().isoformat() if isinstance(next_week_start_date, datetime) else str(next_week_start_date)
    next_week_end_date_str = next_week_end_date.date().isoformat() if isinstance(next_week_end_date, datetime) else str(next_week_end_date)
    
    for assignee_key, assignee_data in by_assignee.items():
        completed_count = len(assignee_data["completed"])
        overdue_count = len(assignee_data["overdue"])
        next_week_count = len(assignee_data["next_week"])
        
        # 构建统计信息，按照示例格式
        # data_source 映射：src1, src2, src3 或其他
        completed_by_source = assignee_data["completed_by_source"]
        overdue_by_source = assignee_data["overdue_by_source"]
        next_week_by_source = assignee_data["next_week_by_source"]
        
        # 统计 others：due_date 没有值的记录数量（从传入的统计中获取）
        overdue_others = no_due_date_count.get(assignee_key, 0) if no_due_date_count else 0
        
        # 统计本周已取消的任务数量（从传入的统计中获取）
        cancelled_count = total_cancelled_count.get(assignee_key, 0) if total_cancelled_count else 0
        
        statistics_item = {
            "total_completed": str(completed_count),  # 本周已完成
            "total_new_created": str(next_week_count),  # 下周待完成
            "total_due_tasks": str(overdue_count),  # 本周未完成
            "total_cancelled": str(cancelled_count),  # 本周已取消
            "due_src1": str(overdue_by_source.get(TodoDataSourceType.PIPELINE_PLAYBOOK.value, 0)),
            "due_src2": str(overdue_by_source.get(TodoDataSourceType.AI_EXTRACTION.value, 0)),
            "due_src3": str(overdue_by_source.get(TodoDataSourceType.MANUAL.value, 0)),
            "next_src1": str(next_week_by_source.get(TodoDataSourceType.PIPELINE_PLAYBOOK.value, 0)),
            "next_src2": str(next_week_by_source.get(TodoDataSourceType.AI_EXTRACTION.value, 0)),
            "next_src3": str(next_week_by_source.get(TodoDataSourceType.MANUAL.value, 0)),
            "completed_src1": str(completed_by_source.get(TodoDataSourceType.PIPELINE_PLAYBOOK.value, 0)),
            "completed_src2": str(completed_by_source.get(TodoDataSourceType.AI_EXTRACTION.value, 0)),
            "completed_src3": str(completed_by_source.get(TodoDataSourceType.MANUAL.value, 0)),
            "others": str(overdue_others),
            "cancelled_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_data['assignee_name']}&due_date__gte={start_date_str}&due_date__lte={end_date_str}&ai_status=CANCELLED"
        }
        
        # 格式化任务明细的日期
        def format_task_date(task):
            """格式化任务日期为 YYYY-MM-DD"""
            due_date_str = task.get("due_date")
            if due_date_str:
                try:
                    if isinstance(due_date_str, str):
                        # 如果是 ISO 格式，提取日期部分
                        if "T" in due_date_str:
                            return due_date_str.split("T")[0]
                        return due_date_str[:10] if len(due_date_str) >= 10 else due_date_str
                except:
                    pass
            return None

        def build_unique_account_task_list(tasks):
            """
            从任务列表中构建仅包含唯一客户名称的任务明细列表。
            目前只输出 account_name，如后续需要可在此处补充其他字段。
            """
            result = []
            seen_accounts = set()
            for task in sorted(tasks, key=lambda x: x.get("due_date") or ""):
                account_name = task.get("account_name")
                # 仅保留有客户名称且未出现过的记录
                if not account_name or account_name in seen_accounts:
                    continue
                seen_accounts.add(account_name)
                task_item = {
                    # "data_source": get_data_source_display_name(task.get("data_source")),
                    "account_name": account_name,
                    # "opportunity_name": task.get("opportunity_name"),
                    # "due_date": format_task_date(task),
                    # "title": task.get("title")
                }
                result.append(task_item)
            return result
        
        # 构建任务明细（仅客户名称，且去重）
        due_task_list = build_unique_account_task_list(assignee_data["overdue"])
        next_week_task_list = build_unique_account_task_list(assignee_data["next_week"])
        
        # 构建单个负责人的结果
        result_item = {
            "start_date": start_date_str,
            "end_date": end_date_str,
            "assignee_name": assignee_data["assignee_name"],
            "assignee_id": assignee_data.get("assignee_id"),
            "statistics": [statistics_item],
            "due_task_list": due_task_list,
            "next_week_task_list": next_week_task_list,
            # 为后续“团队/公司汇总报表”提供更通用的按类型拆分口径（原始 data_source -> count）
            # 注意：现有推送模板不依赖这些字段，新增字段对旧逻辑无破坏。
            "completed_by_source": completed_by_source,
            "overdue_by_source": overdue_by_source,
            "next_week_by_source": next_week_by_source,
            "cancelled_count": cancelled_count,
            "no_due_date_count": overdue_others,
            "due_task_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_data['assignee_name']}&due_date__lte={end_date_str}&is_overdue=True",
            "next_week_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_data['assignee_name']}&due_date__gte={next_week_start_date_str}&due_date__lte={next_week_end_date_str}&ai_status=PENDING&ai_status=IN_PROGRESS",
        }
        
        result_list.append(result_item)
    
    return result_list
