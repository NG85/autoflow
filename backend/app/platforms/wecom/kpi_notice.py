"""部门 / 公司日周报 KPI → 企微精简 text_notice。

飞书仍走 interactive 模板卡；企微只发标题 + 基础信息 +「查看报告」跳转。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional

from app.platforms.wecom.text_notice import build_text_notice_card, new_task_id
from app.utils.push_page_urls import (
    build_company_daily_report_url,
    build_company_weekly_report_url,
    build_team_daily_report_url,
    build_team_weekly_report_url,
)

# _send_notifications_by_platform 的 notification_type → 精简卡规格
_KPI_SPECS: dict[str, dict[str, str]] = {
    "department report": {
        "title": "团队日报",
        "desc": "部门日报",
        "jump": "查看报告",
        "scope": "department_daily",
    },
    "company daily report": {
        "title": "公司日报",
        "desc": "公司日报",
        "jump": "查看报告",
        "scope": "company_daily",
    },
    "weekly report": {
        "title": "团队周报",
        "desc": "部门周报",
        "jump": "查看报告",
        "scope": "department_weekly",
    },
    "company weekly report": {
        "title": "公司周报",
        "desc": "公司周报",
        "jump": "查看报告",
        "scope": "company_weekly",
    },
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value).strip()
    return str(value).strip()


def _parse_day(raw: Any) -> Optional[date]:
    text = _text(raw)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _iso_year_week(raw: Any) -> tuple[int, int]:
    day = _parse_day(raw)
    if day is None:
        return (0, 0)
    year, week, _ = day.isocalendar()
    return (year, week)


def _report_page_url(scope: str, template_vars: Mapping[str, Any]) -> str:
    if scope == "department_daily":
        return build_team_daily_report_url(_text(template_vars.get("report_date")))
    if scope == "company_daily":
        return build_company_daily_report_url(_text(template_vars.get("report_date")))
    start = template_vars.get("start_date") or template_vars.get("report_date")
    year, week = _iso_year_week(start)
    if scope == "department_weekly":
        return build_team_weekly_report_url(year, week)
    if scope == "company_weekly":
        return build_company_weekly_report_url(year, week)
    return ""


def build_kpi_report_wecom_basic_card(
    notification_type: str,
    template_vars: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """按 notification_type 组装企微 KPI 精简卡；不支持的类型返回 None。"""
    spec = _KPI_SPECS.get((notification_type or "").strip())
    if not spec:
        return None
    vars_map = template_vars or {}
    page_url = _report_page_url(spec["scope"], vars_map)
    if not page_url:
        return None

    department = _text(vars_map.get("department_name") or vars_map.get("department"))
    report_date = _text(vars_map.get("report_date"))
    start_date = _text(vars_map.get("start_date"))
    end_date = _text(vars_map.get("end_date"))

    body_lines: list[str] = []
    if department and spec["scope"].startswith("department_"):
        body_lines.append(f"部门：{department}")
    if spec["scope"].endswith("_daily") and report_date:
        body_lines.append(f"报告日：{report_date}")
        title_suffix = report_date
    elif start_date or end_date:
        period = " ~ ".join(part for part in (start_date, end_date) if part)
        body_lines.append(f"周期：{period}")
        title_suffix = period
    else:
        title_suffix = ""

    title = spec["title"]
    if title_suffix:
        title = f"{title}｜{title_suffix}"

    return build_text_notice_card(
        title=title,
        desc=spec["desc"],
        sub_title_text="\n".join(body_lines),
        jump_links=[(spec["jump"], page_url)],
        horizontal_links=(),
        card_action_url=page_url,
        task_id=new_task_id("kpi"),
    )


def supports_kpi_wecom_basic_card(notification_type: str) -> bool:
    return (notification_type or "").strip() in _KPI_SPECS
