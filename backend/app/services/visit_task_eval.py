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


_TASK_STATUS_ZH: Dict[str, str] = {
    "PENDING": "待开始",
    "IN_PROGRESS": "进行中",
    "COMPLETED": "已完成",
    "CANCELLED": "取消",
}


def _task_status_to_zh(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return _TASK_STATUS_ZH.get(text.upper(), text)


def tasks_to_card_payload(changed_tasks: List[Dict[str, Any]]) -> VisitTaskEvalResult:
    """
    将任务列表归一化为拜访卡片 ``tasks`` 变量结构：

    [{"task_status", "task_title", "task_id", "id"}, ...]
    其中 ``id`` 由数组下标生成（1-based 字符串 "1", "2", ...），无需调用方传入。
    也兼容 Aldebaran 原始字段（unique_id / title / status）。
    ``task_status`` 会将英文状态（PENDING / IN_PROGRESS / COMPLETED / CANCELLED）转为中文展示。
    """
    tasks: List[Dict[str, Any]] = []
    for idx, item in enumerate(changed_tasks or [], start=1):
        if not isinstance(item, dict):
            continue
        task_id = item.get("task_id") or item.get("unique_id")
        raw_status = item.get("task_status") or item.get("status")
        tasks.append(
            {
                "task_status": _task_status_to_zh(raw_status),
                "task_title": str(item.get("task_title") or item.get("title") or ""),
                "task_id": str(task_id) if task_id is not None else "",
                "id": str(idx),
            }
        )
    return VisitTaskEvalResult(tasks=tasks, task_count=len(tasks))
