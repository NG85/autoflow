import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body

from app.api.deps import CurrentSuperuserDep

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/crm/visit-metrics/rebuild")
def trigger_rebuild_crm_visit_metrics(
    user: CurrentSuperuserDep,
    entry_week_start: Optional[str] = Body(None, description="录入周开始（YYYY-MM-DD，任意一天即可，会归一到周日）"),
    entry_week_end: Optional[str] = Body(None, description="录入周结束（YYYY-MM-DD，任意一天即可，会归一到周日）"),
    followup_start: Optional[str] = Body(None, description="跟进日期开始（YYYY-MM-DD）"),
    followup_end: Optional[str] = Body(None, description="跟进日期结束（YYYY-MM-DD）"),
):
    """
    手动触发“拜访指标固化”补跑任务（异步，返回 task_id）。

    - 默认：entry-week 重算“当前周+上一周”；followup 重算“最近60天”（可通过 settings 配置）
    - 传参时：按给定区间补跑（entry-week 会按周步进）
    """
    if not user.is_superuser:
        return {"code": 403, "message": "权限不足，只有超级管理员可以触发此任务", "data": {}}

    if (entry_week_start is None) != (entry_week_end is None):
        return {"code": 400, "message": "entry_week_start/entry_week_end 需要同时传或同时不传", "data": {}}
    if (followup_start is None) != (followup_end is None):
        return {"code": 400, "message": "followup_start/followup_end 需要同时传或同时不传", "data": {}}

    # 参数格式校验（不改变含义；实际归周/窗口逻辑在 task 内处理）
    try:
        for x in [entry_week_start, entry_week_end, followup_start, followup_end]:
            if x:
                datetime.strptime(x, "%Y-%m-%d")
    except ValueError:
        return {"code": 400, "message": "日期格式错误，请使用 YYYY-MM-DD", "data": {}}

    try:
        from app.tasks.cron_jobs import rebuild_crm_visit_metrics

        logger.info(
            "用户 %s 手动触发拜访指标补跑任务 entry_week=%s~%s followup=%s~%s",
            getattr(user, "id", ""),
            entry_week_start,
            entry_week_end,
            followup_start,
            followup_end,
        )

        task = rebuild_crm_visit_metrics.delay(
            entry_week_start_str=entry_week_start,
            entry_week_end_str=entry_week_end,
            followup_start_str=followup_start,
            followup_end_str=followup_end,
        )

        return {
            "code": 0,
            "message": "拜访指标补跑任务已触发",
            "data": {
                "task_id": task.id,
                "status": "PENDING",
                "entry_week_start": entry_week_start,
                "entry_week_end": entry_week_end,
                "followup_start": followup_start,
                "followup_end": followup_end,
            },
        }
    except Exception as e:
        logger.exception(f"触发拜访指标补跑任务失败: {e}")
        return {"code": 500, "message": "触发任务失败", "data": {}}

