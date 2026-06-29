"""推送消息中使用的业务页面链接构建（与系统页面链接表保持一致）。"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote, quote_plus

from app.core.config import settings


def _host() -> str:
    return (settings.REVIEW_REPORT_HOST or "").strip().rstrip("/")


def _resolve_path_page_url(path_or_url: str, *, default_path: str) -> str:
    """路径配置与 REVIEW_REPORT_HOST 拼接；也兼容仍配置完整 URL 的环境。"""
    raw = (path_or_url or "").strip() or default_path
    if raw.startswith(("http://", "https://")):
        return raw.rstrip("/")
    host = _host()
    if not host:
        return ""
    path = raw if raw.startswith("/") else f"/{raw}"
    return f"{host}{path}"


def _visit_list_base() -> str:
    return _resolve_path_page_url(
        settings.VISIT_DETAIL_PAGE_URL,
        default_path="/v2/behavior",
    )


def _task_list_base() -> str:
    return _resolve_path_page_url(
        settings.CRM_SALES_TASK_PAGE_URL,
        default_path="/v2/task",
    )


def build_visit_list_page_url(
    *,
    start_date: str,
    end_date: str,
    department_name: Optional[str] = None,
) -> str:
    """拜访列表页（日报卡片 visit_detail_page）。"""
    base = _visit_list_base()
    if not base:
        return ""
    query = (
        f"visit_communication_date_start={quote(start_date, safe='')}"
        f"&visit_communication_date_end={quote(end_date, safe='')}"
    )
    dept = (department_name or "").strip()
    if dept:
        query = f"{query}&department_name={quote_plus(dept)}"
    return f"{base}?{query}"


def build_visit_record_page_url(record_id: str) -> str:
    """拜访详情页（路径参数形式）。"""
    host = _host()
    rid = (record_id or "").strip()
    if not host or not rid:
        return ""
    return f"{host}/v2/behavior/{quote(rid, safe='')}"


def build_visit_record_add_comment_page_url(record_id: str) -> str:
    """拜访卡片内「添加评论/任务」页。"""
    host = _host()
    rid = (record_id or "").strip()
    if not host or not rid:
        return ""
    return f"{host}/v2/behavior/{quote(rid, safe='')}/add-comment"


def build_visit_record_billing_page_url(record_id: str) -> str:
    """拜访计费上报链接（record_id 作为裸 query 值）。"""
    base = _visit_list_base()
    rid = (record_id or "").strip()
    if not base:
        return "about:blank"
    if not rid:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{quote(rid, safe='')}"


def build_task_list_page_url(
    *,
    owner_name: Optional[str] = None,
    department_name: Optional[str] = None,
    due_date__gte: Optional[str] = None,
    due_date__lte: Optional[str] = None,
    is_overdue: Optional[bool] = None,
    ai_status__in: Optional[list[str]] = None,
) -> str:
    """任务列表页（日报 / 销售任务周卡片等）。"""
    base = _task_list_base()
    if not base:
        return ""
    parts: list[str] = []
    if department_name:
        parts.append(f"department_name={quote_plus(department_name.strip())}")
    if owner_name:
        parts.append(f"owner_name={quote_plus(owner_name.strip())}")
    if due_date__gte:
        parts.append(f"due_date__gte={quote(due_date__gte, safe='')}")
    if due_date__lte:
        parts.append(f"due_date__lte={quote(due_date__lte, safe='')}")
    if is_overdue is not None:
        parts.append(f"is_overdue={'true' if is_overdue else 'false'}")
    for status in ai_status__in or []:
        value = (status or "").strip()
        if value:
            parts.append(f"ai_status__in={quote(value, safe='')}")
    if not parts:
        return base
    return f"{base}?{'&'.join(parts)}"


def build_task_detail_page_url(task_id: str) -> str:
    """单任务详情页（任务创建通知兜底链接）。"""
    base = _task_list_base()
    tid = (task_id or "").strip()
    if not base or not tid:
        return base
    return f"{base}/{quote(tid, safe='')}"


def build_weekly_review_1_page_url(execution_id: str) -> str:
    """周报表1（review1 / review1s）。"""
    host = _host()
    eid = (execution_id or "").strip()
    if not host or not eid:
        return host or ""
    return f"{host}/v2/business/weekly-review/{quote(eid, safe='')}"


def build_weekly_review_5_page_url(execution_id: str) -> str:
    """周报表5（review5）。"""
    host = _host()
    eid = (execution_id or "").strip()
    if not host or not eid:
        return host or ""
    return f"{host}/v2/business/behavior-analysis/{quote(eid, safe='')}"


def build_weekly_followup_summary_page_url(
    *,
    week_start: str,
    week_end: str,
    department_name: Optional[str] = None,
) -> str:
    """周跟进汇总页（部门 / 公司周报卡片、周跟进评论通知）。"""
    host = _host()
    if not host:
        return ""
    parts = [
        f"week_start={quote(week_start, safe='')}",
        f"week_end={quote(week_end, safe='')}",
    ]
    dept = (department_name or "").strip()
    if dept:
        parts.append(f"department_name={quote_plus(dept)}")
    return f"{host}/v2/business/followup-summary/detail?{'&'.join(parts)}"
