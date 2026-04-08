from celery import Celery
from celery.schedules import crontab
from app.core.config import settings, WritebackFrequency


app = Celery(
    settings.PROJECT_NAME,
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

app.conf.update(
    timezone='Asia/Shanghai',
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Worker 内存回收与任务超时
    worker_max_memory_per_child=settings.CELERY_MAX_MEMORY_PER_CHILD or None,
    worker_max_tasks_per_child=settings.CELERY_MAX_TASKS_PER_CHILD or None,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    result_expires=settings.CELERY_RESULT_EXPIRES,
    # 配置队列路由
    task_routes=[
        # {"app.tasks.evaluate.*": {"queue": "evaluation"}},
        {"app.tasks.cron_jobs.generate_crm_daily_statistics": {"queue": "cron"}},
        {"app.tasks.cron_jobs.generate_crm_weekly_report": {"queue": "cron"}},
        {"app.tasks.cron_jobs.generate_crm_weekly_followup_summary": {"queue": "cron"}},
        {"app.tasks.cron_jobs.send_crm_weekly_followup_leader_engagement_report": {"queue": "cron"}},
        {"app.tasks.cron_jobs.crm_visit_records_writeback": {"queue": "cron"}},
        # Metrics 固化：单独队列，避免与 cron 任务抢资源
        {"app.tasks.cron_jobs.rebuild_crm_visit_metrics": {"queue": "metrics"}},
        {"app.tasks.cron_jobs.rebuild_crm_todo_metrics": {"queue": "metrics"}},
        {"app.tasks.cron_jobs.persist_crm_todo_facts_hourly_stock": {"queue": "metrics"}},
        {"app.tasks.cron_jobs.send_sales_task_summary": {"queue": "cron"}},
        {"app.tasks.bitable_import.*": {"queue": "cron"}},
        {"app.tasks.document_qa.*": {"queue": "llm"}},
        {"*": {"queue": "default"}},
    ],
    broker_connection_retry_on_startup=True,
)

app.autodiscover_tasks(["app"])

# 导入定时任务模块
app.autodiscover_tasks(['app.tasks.cron_jobs'])

# 导入 review 索引任务模块
app.autodiscover_tasks(['app.tasks.review_index'])

# 配置定时任务
if settings.CRM_DAILY_TASK_ENABLED:
    cron_expr = settings.CRM_DAILY_TASK_CRON
    # 解析crontab表达式
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        daily_schedule = crontab(
            minute=minute, 
            hour=hour, 
            day_of_month=day_of_month, 
            month_of_year=month_of_year, 
            day_of_week=day_of_week
        )
    else:
        # 默认值：每天早上10点
        daily_schedule = crontab(hour=10, minute=0)
    
    app.conf.beat_schedule = {
        'create-crm-daily-datasource': {
            'task': 'app.tasks.cron_jobs.create_crm_daily_datasource',
            'schedule': daily_schedule,
        },
    }

if not hasattr(app.conf, 'beat_schedule') or app.conf.beat_schedule is None:
    app.conf.beat_schedule = {}

# 只在开关打开时添加新任务
if settings.ENABLE_FEISHU_BTABLE_SYNC:
    cron_expr = settings.FEISHU_BTABLE_SYNC_CRON
    # 解析crontab表达式
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        schedule = crontab(minute=minute, hour=hour, day_of_month=day_of_month, month_of_year=month_of_year, day_of_week=day_of_week)
    else:
        # 默认值：每天早上00:05
        schedule = crontab(hour=0, minute=5)
    app.conf.beat_schedule = getattr(app.conf, 'beat_schedule', {})
    app.conf.beat_schedule['sync_bitable_visit_records'] = {
        'task': 'app.tasks.bitable_import.sync_bitable_visit_records',
        'schedule': schedule,
    }

# CRM日报统计任务
if settings.CRM_DAILY_REPORT_ENABLED:
    cron_expr = settings.CRM_DAILY_REPORT_CRON
    # 解析crontab表达式
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        statistics_schedule = crontab(
            minute=minute, 
            hour=hour, 
            day_of_month=day_of_month, 
            month_of_year=month_of_year, 
            day_of_week=day_of_week
        )
    else:
        # 默认值：每天早上8:30
        statistics_schedule = crontab(hour=8, minute=30)
    
    app.conf.beat_schedule = getattr(app.conf, 'beat_schedule', {})
    app.conf.beat_schedule['generate_crm_daily_report'] = {
        'task': 'app.tasks.cron_jobs.generate_crm_daily_statistics',
        'schedule': statistics_schedule,
    }

# CRM周报推送任务
if settings.CRM_WEEKLY_REPORT_ENABLED:
    cron_expr = settings.CRM_WEEKLY_REPORT_CRON
    # 解析crontab表达式
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        weekly_schedule = crontab(
            minute=minute, 
            hour=hour, 
            day_of_month=day_of_month, 
            month_of_year=month_of_year, 
            day_of_week=day_of_week
        )
    else:
        # 默认值：每周日上午11点
        weekly_schedule = crontab(hour=11, minute=0, day_of_week=0)
    
    app.conf.beat_schedule = getattr(app.conf, 'beat_schedule', {})
    app.conf.beat_schedule['generate_crm_weekly_report'] = {
        'task': 'app.tasks.cron_jobs.generate_crm_weekly_report',
        'schedule': weekly_schedule,
    }

# CRM周跟进总结任务（公司/部门 + 列表明细）
if settings.CRM_WEEKLY_FOLLOWUP_ENABLED:
    cron_expr = settings.CRM_WEEKLY_FOLLOWUP_CRON
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        weekly_followup_schedule = crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
    else:
        # 默认：每周日上午9:30（需早于 generate_crm_weekly_report）
        weekly_followup_schedule = crontab(hour=9, minute=30, day_of_week=0)

    app.conf.beat_schedule = getattr(app.conf, 'beat_schedule', {})
    app.conf.beat_schedule['generate_crm_weekly_followup_summary'] = {
        'task': 'app.tasks.cron_jobs.generate_crm_weekly_followup_summary',
        'schedule': weekly_followup_schedule,
    }

# CRM周跟进总结 leader 已阅/评论统计（周一 9:00）
if settings.CRM_WEEKLY_FOLLOWUP_ENGAGEMENT_ENABLED:
    cron_expr = settings.CRM_WEEKLY_FOLLOWUP_ENGAGEMENT_CRON
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        weekly_followup_engagement_schedule = crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
    else:
        weekly_followup_engagement_schedule = crontab(hour=9, minute=0, day_of_week=1)

    app.conf.beat_schedule = getattr(app.conf, "beat_schedule", {})
    app.conf.beat_schedule["send_crm_weekly_followup_leader_engagement_report"] = {
        "task": "app.tasks.cron_jobs.send_crm_weekly_followup_leader_engagement_report",
        "schedule": weekly_followup_engagement_schedule,
    }

# CRM拜访记录回写任务
if settings.CRM_WRITEBACK_ENABLED:
    # 解析cron表达式
    cron_expr = settings.CRM_WRITEBACK_CRON
    cron_fields = cron_expr.strip().split()
    
    if len(cron_fields) == 5:
        # 使用配置的cron表达式
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        writeback_schedule = crontab(
            minute=minute, 
            hour=hour, 
            day_of_month=day_of_month, 
            month_of_year=month_of_year, 
            day_of_week=day_of_week
        )
    else:
        # 根据频率配置使用默认值
        if settings.CRM_WRITEBACK_FREQUENCY == WritebackFrequency.DAILY:
            # 按天回写：每天下午2点
            writeback_schedule = crontab(hour=14, minute=0)
        else:  # weekly
            # 按周回写：每周日下午2点
            writeback_schedule = crontab(hour=14, minute=0, day_of_week=0)
    
    app.conf.beat_schedule = getattr(app.conf, 'beat_schedule', {})
    app.conf.beat_schedule['crm_visit_records_writeback'] = {
        'task': 'app.tasks.cron_jobs.crm_visit_records_writeback',
        'schedule': writeback_schedule,
    }

# CRM销售任务推送任务
if settings.CRM_SALES_TASK_ENABLED:
    cron_expr = settings.CRM_SALES_TASK_CRON
    # 解析crontab表达式
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        sales_task_schedule = crontab(
            minute=minute, 
            hour=hour, 
            day_of_month=day_of_month, 
            month_of_year=month_of_year, 
            day_of_week=day_of_week
        )
    else:
        # 默认值：每周六上午10点
        sales_task_schedule = crontab(hour=10, minute=0, day_of_week=6)
    
    app.conf.beat_schedule = getattr(app.conf, 'beat_schedule', {})
    app.conf.beat_schedule['send_sales_task_summary'] = {
        'task': 'app.tasks.cron_jobs.send_sales_task_summary',
        'schedule': sales_task_schedule,
    }

# CRM拜访指标固化任务（每小时，默认整点）
if settings.CRM_VISIT_METRICS_ENABLED:
    cron_expr = settings.CRM_VISIT_METRICS_CRON
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        visit_metrics_schedule = crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
    else:
        visit_metrics_schedule = crontab(minute=0)

    app.conf.beat_schedule = getattr(app.conf, "beat_schedule", {})
    app.conf.beat_schedule["rebuild_crm_visit_metrics"] = {
        "task": "app.tasks.cron_jobs.rebuild_crm_visit_metrics",
        "schedule": visit_metrics_schedule,
    }

# CRM销售任务指标固化任务（每小时，默认整点）
if settings.CRM_TODO_METRICS_ENABLED:
    cron_expr = settings.CRM_TODO_METRICS_CRON
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        todo_metrics_schedule = crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
    else:
        todo_metrics_schedule = crontab(minute=0)

    app.conf.beat_schedule = getattr(app.conf, "beat_schedule", {})
    app.conf.beat_schedule["rebuild_crm_todo_metrics"] = {
        "task": "app.tasks.cron_jobs.rebuild_crm_todo_metrics",
        "schedule": todo_metrics_schedule,
    }

# CRM todo facts hourly snapshot（默认关闭；开启后每小时写入当天 hour 粒度快照）
if settings.CRM_TODO_FACTS_HOURLY_ENABLED:
    cron_expr = settings.CRM_TODO_FACTS_HOURLY_CRON
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        todo_facts_hourly_schedule = crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        )
    else:
        todo_facts_hourly_schedule = crontab(minute=0)

    app.conf.beat_schedule = getattr(app.conf, "beat_schedule", {})
    app.conf.beat_schedule["persist_crm_todo_facts_hourly_stock"] = {
        "task": "app.tasks.cron_jobs.persist_crm_todo_facts_hourly_stock",
        "schedule": todo_facts_hourly_schedule,
    }
