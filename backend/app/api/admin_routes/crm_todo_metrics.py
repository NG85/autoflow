import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body

from app.api.deps import CurrentSuperuserDep

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/crm/todo-metrics/rebuild")
def trigger_rebuild_crm_todo_metrics(
    user: CurrentSuperuserDep,
    week_start: Optional[str] = Body(None, description="周开始（YYYY-MM-DD，任意一天即可，会归一到周日）"),
    week_end: Optional[str] = Body(None, description="周结束（YYYY-MM-DD，任意一天即可，会归一到周日）"),
):
    """
    手动触发“销售任务指标固化”补跑任务（异步，返回 task_id）。

    - 默认：重算“当前周+上一周”（created 指标按创建时间落周；存量指标写当前周）
    - 传参：按给定周区间补跑 created 指标
    """
    if not user.is_superuser:
        return {"code": 403, "message": "权限不足，只有超级管理员可以触发此任务", "data": {}}

    if (week_start is None) != (week_end is None):
        return {"code": 400, "message": "week_start/week_end 需要同时传或同时不传", "data": {}}

    try:
        for x in [week_start, week_end]:
            if x:
                datetime.strptime(x, "%Y-%m-%d")
    except ValueError:
        return {"code": 400, "message": "日期格式错误，请使用 YYYY-MM-DD", "data": {}}

    try:
        from app.tasks.cron_jobs import rebuild_crm_todo_metrics

        logger.info(
            "用户 %s 手动触发销售任务指标补跑任务 week=%s~%s",
            getattr(user, "id", ""),
            week_start,
            week_end,
        )

        task = rebuild_crm_todo_metrics.delay(
            week_start_str=week_start,
            week_end_str=week_end,
        )

        return {
            "code": 0,
            "message": "销售任务指标补跑任务已触发",
            "data": {"task_id": task.id, "status": "PENDING", "week_start": week_start, "week_end": week_end},
        }
    except Exception as e:
        logger.exception(f"触发销售任务指标补跑任务失败: {e}")
        return {"code": 500, "message": "触发任务失败", "data": {}}


@router.post("/crm/todo-facts/hourly-stock/persist")
def trigger_persist_crm_todo_facts_hourly_stock(
    user: CurrentSuperuserDep,
):
    """
    手动触发“销售任务指标 facts 小时级存量快照”（异步，返回 task_id）。

    当前口径：
    - company 级
    - due_date IS NULL 存量
    - 按 data_source 三条线（MANUAL/AI_EXTRACTION/PIPELINE_PLAYBOOK）
    - 写入 crm_todo_metrics_facts：anchor=stock, grain=hour
    """
    if not user.is_superuser:
        return {"code": 403, "message": "权限不足，只有超级管理员可以触发此任务", "data": {}}

    try:
        from app.tasks.cron_jobs import persist_crm_todo_facts_hourly_stock

        logger.info(
            "用户 %s 手动触发销售任务 facts hourly 存量快照任务",
            getattr(user, "id", ""),
        )

        task = persist_crm_todo_facts_hourly_stock.delay()

        return {
            "code": 0,
            "message": "销售任务 facts hourly 存量快照任务已触发",
            "data": {"task_id": task.id, "status": "PENDING"},
        }
    except Exception as e:
        logger.exception(f"触发销售任务 facts hourly 存量快照任务失败: {e}")
        return {"code": 500, "message": "触发任务失败", "data": {}}

