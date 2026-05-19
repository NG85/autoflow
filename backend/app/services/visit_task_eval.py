"""拜访卡片任务变量归一化（由 Aldebaran 回调 /notification/push 时传入）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class VisitTaskEvalResult:
    tasks: List[Dict[str, Any]]
    task_count: int

    @classmethod
    def empty(cls) -> VisitTaskEvalResult:
        return cls(tasks=[], task_count=0)


def tasks_to_card_payload(changed_tasks: List[Dict[str, Any]]) -> VisitTaskEvalResult:
    """
    将任务列表归一化为拜访卡片 ``tasks`` 变量结构：

    [{"task_status", "task_title", "task_id", "id"}, ...]
    其中 ``id`` 由数组下标生成（1-based 字符串 "1", "2", ...），无需调用方传入。
    也兼容 Aldebaran 原始字段（unique_id / title / status）。
    """
    tasks: List[Dict[str, Any]] = []
    for idx, item in enumerate(changed_tasks or [], start=1):
        if not isinstance(item, dict):
            continue
        task_id = item.get("task_id") or item.get("unique_id")
        tasks.append(
            {
                "task_status": str(item.get("task_status") or item.get("status") or ""),
                "task_title": str(item.get("task_title") or item.get("title") or ""),
                "task_id": str(task_id) if task_id is not None else "",
                "id": str(idx),
            }
        )
    return VisitTaskEvalResult(tasks=tasks, task_count=len(tasks))
