import logging
from datetime import datetime, timedelta
from enum import Enum
from sqlmodel import Session
import pytz

from app.core.config import settings, WritebackMode, WritebackFrequency
from app.core.db import engine
from app.celery import app
from app.models import DataSource, DataSourceType
from app.repositories import knowledge_base_repo
from app.services.crm_statistics_service import crm_statistics_service
from app.services.platform_notification_service import platform_notification_service
from app.services.crm_writeback_service import crm_writeback_service
from app.tasks.knowledge_base import import_documents_from_kb_datasource

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
def generate_crm_daily_statistics(self, target_date_str=None):
    """
    生成CRM销售日报完整数据并推送飞书通知
    每天早上8:30执行，处理前一天的销售日报数据
    
    Args:
        target_date_str: 目标日期字符串，格式YYYY-MM-DD，不传则默认为昨天
    
    工作流程：
    1. 从crm_daily_account_statistics表查询每个销售的统计数据
    2. 通过correlation_id关联crm_account_assessment表获取评估详情
    3. 组合成完整的日报数据
    
    推送逻辑（根据是否有数据）：
    - 有数据时（sales_count > 0）：
      * 推送个人日报飞书卡片给每个有数据的销售人员本人
      * 按部门汇总数据生成部门日报（包括有数据和没数据的部门）
      * 推送部门日报飞书卡片给各部门负责人
      * 汇总公司级数据生成公司日报
      * 推送公司日报飞书卡片（内部环境推送到群聊，外部环境推送给管理员）
    
    - 无数据时（sales_count == 0）：
      * 个人日报：不推送（因为没有统计数据，说明当天没有销售活动）
      * 部门日报：推送空数据的部门日报给所有有负责人的部门
      * 公司日报：推送空数据的公司日报
    
    注意：所有推送都受CRM_DAILY_REPORT_FEISHU_ENABLED开关控制
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
            # 生成指定日期的完整日报数据
            # - 个人日报：仅在 sales_count > 0 时推送
            # - 部门日报和公司日报：无论 sales_count 是多少，都会推送（可能为空数据）
            sales_count = crm_statistics_service.generate_daily_statistics(session, target_date)
            
            # 生成简化的返回信息（用于任务状态查询）
            if sales_count > 0:
                logger.info(f"CRM日报数据生成完成，处理了 {sales_count} 个销售人员的数据")
                message = f"成功处理了 {sales_count} 个销售人员的日报数据"
            else:
                logger.warning(f"{target_date} 没有找到任何销售人员的数据，但部门日报和公司日报已在 generate_daily_statistics 中处理")
                message = "没有找到销售数据，部门日报和公司日报已生成（可能为空数据）"
            
            # 返回简化的结果（用于任务状态查询 API）
            return {
                "target_date": target_date.isoformat(),
                "sales_count": sales_count,
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
    2. 从crm_daily_account_statistics表查询该时间范围的统计数据
    3. 按部门汇总数据生成部门周报
    4. 推送部门周报飞书卡片给各部门负责人
    5. 生成并推送公司周报给管理团队
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
            today = datetime.now().date()
            # 计算上周日（今天往前推7天，然后找到最近的周日）
            days_since_sunday = (today.weekday() + 1) % 7  # 0=周一，1=周二，...，6=周日
            last_sunday = today - timedelta(days=days_since_sunday + 7)
            this_saturday = last_sunday + timedelta(days=6)
            
            start_date = last_sunday
            end_date = this_saturday
            logger.info(f"开始执行CRM周报数据生成任务，默认处理上周日到本周六: {start_date} 到 {end_date}")
        
        with Session(engine) as session:
            # 生成指定日期范围的部门周报数据
            department_reports = crm_statistics_service.aggregate_department_weekly_reports(
                session=session,
                start_date=start_date,
                end_date=end_date
            )
            
            if not department_reports:
                logger.warning(f"{start_date} 到 {end_date} 没有找到任何周报数据")
                return {
                    "success": False,
                    "message": f"{start_date} 到 {end_date} 没有找到任何周报数据",
                    "data": {}
                }
            
            logger.info(f"CRM部门周报数据生成完成，共 {len(department_reports)} 个部门")
            
            # 生成公司周报数据
            company_weekly_report = crm_statistics_service.aggregate_company_weekly_report(
                session=session,
                start_date=start_date,
                end_date=end_date
            )
            
            if company_weekly_report:
                logger.info("CRM公司周报数据生成完成")
                company_report_generated = True
            else:
                logger.warning(f"{start_date} 到 {end_date} 没有找到任何公司周报数据")
                company_report_generated = False
            
            # 如果启用了飞书推送，发送周报通知
            if settings.CRM_WEEKLY_REPORT_FEISHU_ENABLED:
                department_success_count = 0
                department_failed_count = 0
                company_success = False
                
                if not report_type or report_type == 'department':
                    # 发送部门周报通知
                    for department_report in department_reports:
                        try:
                            # 发送部门周报通知给部门负责人
                            result = platform_notification_service.send_weekly_report_notification(
                                db_session=session,
                                department_report_data=department_report
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
                    "feishu_enabled": False,
                    "message": f"成功处理了 {len(department_reports)} 个部门的周报数据，{'生成' if company_report_generated else '未生成'}公司周报数据，飞书推送已禁用"
                }
                
    except Exception as e:
        logger.exception(f"CRM周报数据生成任务执行失败: {e}")
        # 使用Celery的重试机制
        self.retry(exc=e, countdown=300)  # 5分钟后重试


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
       - CBG模式：按客户和商机分组处理拜访记录，生成格式化的回写内容
       - APAC模式：为每条拜访记录创建Salesforce的任务
       - OLM模式：为每条拜访记录创建销售易的拜访记录
       - CHAITIN模式：为每条拜访记录创建长亭的拜访记录
    4. 调用相应的API进行回写或任务创建
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
        
        with Session(engine) as session:
            # 执行拜访记录回写
            result = crm_writeback_service.writeback_visit_records(
                session=session,
                start_date=start_date,
                end_date=end_date,
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
            today = datetime.now().date()
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
            
            if not this_week_sales_tasks and not next_week_sales_tasks:
                logger.info("crm_todos 未找到需要推送的销售任务")
                return {
                    "success": True,
                    "message": "没有需要推送的销售任务",
                    "processed_count": 0,
                    "success_count": 0,
                    "failed_count": 0
                }

            logger.info(f"crm_todos 读取到本周已完成 {len(this_week_sales_tasks)} 条，下周待完成 {len(next_week_sales_tasks)} 条，截止到本周的全部逾期任务 {len(overdue_tasks)} 条，本周已取消 {total_cancelled_count} 条(涉及 {len(cancelled_by_assignee_count)} 个负责人)，due_date为空 {total_no_due_date} 条（涉及 {len(no_due_date_count)} 个负责人）")
            
            # 按负责人统计任务（本周和下周的任务都有due_date，due_date为空的单独统计）
            analyze_results = _analyze_crm_todos(this_week_sales_tasks, next_week_sales_tasks, overdue_tasks, start_date, end_date, next_week_start_date, next_week_end_date, no_due_date_count, cancelled_by_assignee_count)
            logger.info(f"分析结果: {len(analyze_results)} 个负责人的任务统计")
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
            "completed_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_data['assignee_name']}&due_date__gte={start_date_str}&due_date__lte={end_date_str}&ai_status=COMPLETED",
            "due_task_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_data['assignee_name']}&due_date__lte={end_date_str}&is_overdue=True",
            "cancelled_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_data['assignee_name']}&due_date__gte={start_date_str}&due_date__lte={end_date_str}&ai_status=CANCELLED",
            "next_week_query_url": f"{settings.CRM_SALES_TASK_PAGE_URL}?owner_name={assignee_data['assignee_name']}&due_date__gte={next_week_start_date_str}&due_date__lte={next_week_end_date_str}&ai_status=PENDING&ai_status=IN_PROGRESS",
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
        
        # 构建任务明细
        due_task_list = []
        for task in sorted(assignee_data["overdue"], key=lambda x: x.get("due_date") or ""):
            task_item = {
                "data_source": get_data_source_display_name(task.get("data_source")),
                "account_name": task.get("account_name"),
                "opportunity_name": task.get("opportunity_name"),
                "due_date": format_task_date(task),
                "title": task.get("title")
            }
            due_task_list.append(task_item)
        
        next_week_task_list = []
        for task in sorted(assignee_data["next_week"], key=lambda x: x.get("due_date") or ""):
            task_item = {
                "data_source": get_data_source_display_name(task.get("data_source")),
                "account_name": task.get("account_name"),
                "opportunity_name": task.get("opportunity_name"),
                "due_date": format_task_date(task),
                "title": task.get("title")
            }
            next_week_task_list.append(task_item)
        
        # 构建单个负责人的结果
        result_item = {
            "start_date": start_date_str,
            "end_date": end_date_str,
            "assignee_name": assignee_data["assignee_name"],
            "statistics": [statistics_item],
            "due_task_list": due_task_list,
            "next_week_task_list": next_week_task_list
        }
        
        result_list.append(result_item)
    
    return result_list
