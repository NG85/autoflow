from app.core.config import settings
from app.tasks.knowledge_base import import_documents_from_kb_datasource
from app.models import DataSource, DataSourceType
from app.repositories import knowledge_base_repo
from app.core.db import engine
from sqlmodel import Session
from datetime import datetime, timedelta
import logging
from app.celery import app

logger = logging.getLogger(__name__)

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
    4. 推送个人日报飞书卡片给每个销售人员本人
    5. 按部门汇总数据生成部门日报
    6. 推送部门日报飞书卡片给各部门负责人
    7. 汇总公司级数据生成公司日报
    8. 推送公司日报飞书卡片（内部环境推送到群聊，外部环境推送给管理员）
    """
    try:
        from app.services.crm_daily_statistics_service import crm_daily_statistics_service
        from datetime import datetime, timedelta
        
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
            sales_count = crm_daily_statistics_service.generate_daily_statistics(session, target_date)
            
            if sales_count > 0:
                logger.info(f"CRM日报数据生成完成，处理了 {sales_count} 个销售人员的数据")
                from app.core.config import settings
                feishu_status = "已推送" if settings.CRM_DAILY_REPORT_FEISHU_ENABLED else "已禁用"
                
                # 如果启用了飞书推送，统计部门数量
                department_count = 0
                if settings.CRM_DAILY_REPORT_FEISHU_ENABLED:
                    try:
                        department_reports = crm_daily_statistics_service.aggregate_department_reports(session, target_date)
                        department_count = len(department_reports)
                    except Exception as e:
                        logger.warning(f"统计部门数量失败: {e}")
                
                return {
                    "success": True,
                    "target_date": target_date.isoformat(),
                    "sales_count": sales_count,
                    "department_count": department_count,
                    "company_report_generated": settings.CRM_DAILY_REPORT_FEISHU_ENABLED,
                    "feishu_enabled": settings.CRM_DAILY_REPORT_FEISHU_ENABLED,
                    "feishu_status": feishu_status,
                    "message": f"成功处理了 {sales_count} 个销售人员的数据，{department_count} 个部门，生成公司日报，飞书推送: {feishu_status}"
                }
            else:
                logger.warning(f"{target_date} 没有找到任何销售人员的数据，可能是节假日或数据未同步")
                return {
                    "success": True,
                    "target_date": target_date.isoformat(),
                    "sales_count": 0,
                    "feishu_enabled": False,
                    "feishu_status": "无数据",
                    "message": "没有找到销售数据"
                }
            
    except Exception as e:
        logger.error(f"CRM日报数据生成任务失败: {str(e)}")
        # 使用Celery的重试机制
        self.retry(exc=e, countdown=300)  # 5分钟后重试




@app.task(bind=True, max_retries=3)
def generate_crm_weekly_report(self, start_date_str=None, end_date_str=None):
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
        from app.services.crm_daily_statistics_service import crm_daily_statistics_service
        from app.services.feishu_notification_service import FeishuNotificationService
        from datetime import datetime, timedelta
        
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
            department_reports = crm_daily_statistics_service.aggregate_department_weekly_reports(
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
            
            logger.info(f"CRM周报数据生成完成，共 {len(department_reports)} 个部门")
            
            # 如果启用了飞书推送，发送周报通知
            if settings.CRM_WEEKLY_REPORT_FEISHU_ENABLED:
                notification_service = FeishuNotificationService()
                department_success_count = 0
                department_failed_count = 0
                
                # 发送部门周报通知
                for department_report in department_reports:
                    try:
                        # 发送部门周报通知给部门负责人
                        result = notification_service.send_weekly_report_notification(
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
                
                # 生成并发送公司周报通知
                try:
                    company_weekly_report = crm_daily_statistics_service.aggregate_company_weekly_report(
                        session=session,
                        start_date=start_date,
                        end_date=end_date
                    )
                    
                    if company_weekly_report:
                        company_result = notification_service.send_company_weekly_report_notification(
                            company_weekly_report_data=company_weekly_report
                        )
                        
                        if company_result["success"]:
                            logger.info(
                                f"成功发送公司周报通知，"
                                f"推送成功 {company_result['success_count']}/{company_result['recipients_count']} 次"
                            )
                        else:
                            logger.warning(f"公司周报通知发送失败: {company_result['message']}")
                    else:
                        logger.warning(f"{start_date} 到 {end_date} 没有找到任何数据，跳过公司周报推送")
                        
                except Exception as e:
                    logger.error(f"发送公司周报通知时出错: {str(e)}")
                
                logger.info(f"CRM周报飞书通知发送完成: 部门周报成功 {department_success_count} 个，失败 {department_failed_count} 个，公司周报已发送")
                
                return {
                    "success": True,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "department_count": len(department_reports),
                    "company_report_generated": True,
                    "feishu_enabled": True,
                    "department_success_count": department_success_count,
                    "department_failed_count": department_failed_count,
                    "message": f"成功处理了 {len(department_reports)} 个部门的周报数据，生成公司周报，飞书推送: 部门周报成功 {department_success_count} 个，失败 {department_failed_count} 个"
                }
            else:
                logger.info("飞书推送已禁用，仅生成周报数据")
                return {
                    "success": True,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "department_count": len(department_reports),
                    "feishu_enabled": False,
                    "message": f"成功处理了 {len(department_reports)} 个部门的周报数据，飞书推送已禁用"
                }
                
    except Exception as e:
        logger.exception(f"CRM周报数据生成任务执行失败: {e}")
        return {
            "success": False,
            "message": f"任务执行失败: {str(e)}",
            "data": {}
        }
