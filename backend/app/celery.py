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
