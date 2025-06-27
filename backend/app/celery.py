from celery import Celery
from celery.schedules import crontab
from app.core.config import settings


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
    task_routes=[
        {"app.tasks.evaluate.*": {"queue": "evaluation"}},
        {"*": {"queue": "default"}},
    ],
    broker_connection_retry_on_startup=True,
)

app.autodiscover_tasks(["app"])

# 导入定时任务模块
app.autodiscover_tasks(['app.tasks.cron_jobs'])

# 配置定时任务
if settings.CRM_DAILY_TASK_ENABLED:
    app.conf.beat_schedule = {
        'create-crm-daily-datasource': {
            'task': 'app.tasks.cron_jobs.create_crm_daily_datasource',
            'schedule': crontab(
                hour=settings.CRM_DAILY_TASK_HOUR,
                minute=settings.CRM_DAILY_TASK_MINUTE
            ),
        },
    }

if not hasattr(app.conf, 'beat_schedule') or app.conf.beat_schedule is None:
    app.conf.beat_schedule = {}

# 只在开关打开时添加新任务
if settings.ENABLE_FEISHU_BTABLE_SYNC:
    from celery.schedules import crontab
    cron_expr = settings.FEISHU_BTABLE_SYNC_CRON
    # 解析crontab表达式
    cron_fields = cron_expr.strip().split()
    if len(cron_fields) == 5:
        minute, hour, day_of_month, month_of_year, day_of_week = cron_fields
        schedule = crontab(minute=minute, hour=hour, day_of_month=day_of_month, month_of_year=month_of_year, day_of_week=day_of_week)
    else:
        schedule = crontab(hour=0, minute=5)
    app.conf.beat_schedule = getattr(app.conf, 'beat_schedule', {})
    app.conf.beat_schedule['sync_bitable_visit_records'] = {
        'task': 'app.tasks.bitable_import.sync_bitable_visit_records',
        'schedule': schedule,
    }
