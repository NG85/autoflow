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
    
