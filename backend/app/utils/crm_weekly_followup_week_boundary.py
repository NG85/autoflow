"""周跟进总结：可配置周界（起止星期）与 period 解析。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal, Tuple

from app.core.config import CRMWeeklyFollowupWeekPreset, settings

WeeklyFollowupWeekRangeMode = Literal["completed", "in_progress"]

_PERIOD_RE = re.compile(r"^(\d{4})-W(\d{1,2})$", re.IGNORECASE)

# Python weekday(): 周一=0 … 周日=6
_PRESET_BOUNDARIES: dict[CRMWeeklyFollowupWeekPreset, tuple[int, int]] = {
    CRMWeeklyFollowupWeekPreset.SAT_FRI: (5, 4),
    CRMWeeklyFollowupWeekPreset.SUN_SAT: (6, 5),
    CRMWeeklyFollowupWeekPreset.MON_SUN: (0, 6),
}

_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def get_weekly_followup_week_boundary() -> tuple[int, int]:
    """
    返回 (week_start_weekday, week_end_weekday)。
    显式配置 CRM_WEEKLY_FOLLOWUP_WEEK_START/END_WEEKDAY 时优先于预设。
    """
    start = settings.CRM_WEEKLY_FOLLOWUP_WEEK_START_WEEKDAY
    end = settings.CRM_WEEKLY_FOLLOWUP_WEEK_END_WEEKDAY
    if start is not None and end is not None:
        return start, end
    return _PRESET_BOUNDARIES[settings.CRM_WEEKLY_FOLLOWUP_WEEK_PRESET]


def weekly_followup_week_boundary_label() -> str:
    """用于日志，如「周六~周五」。"""
    start_wd, end_wd = get_weekly_followup_week_boundary()
    return f"{_WEEKDAY_LABELS[start_wd]}~{_WEEKDAY_LABELS[end_wd]}"


def _week_start_containing(d: date, *, start_weekday: int) -> date:
    return d - timedelta(days=(d.weekday() - start_weekday) % 7)


def _week_span_days(*, start_weekday: int, end_weekday: int) -> int:
    """周起始日到结束日的天数差（含结束日，固定 7 天自然周）。"""
    return (end_weekday - start_weekday) % 7


def _iso_weekday_from_python(end_weekday: int) -> int:
    """Python weekday → ISO 1=周一 … 7=周日，供 fromisocalendar 使用。"""
    return end_weekday + 1 if end_weekday < 6 else 7


def resolve_weekly_followup_week_range(
    today: date,
    *,
    week_range_mode: WeeklyFollowupWeekRangeMode = "completed",
) -> Tuple[date, date]:
    """
    按配置周界计算周跟进统计区间。

    - completed：上一完整自然周
    - in_progress：当前自然周，week_end 不超过 today
    """
    start_wd, end_wd = get_weekly_followup_week_boundary()
    span = _week_span_days(start_weekday=start_wd, end_weekday=end_wd)

    if week_range_mode == "in_progress":
        week_start = _week_start_containing(today, start_weekday=start_wd)
        week_end = min(today, week_start + timedelta(days=span))
        return week_start, week_end

    this_week_start = _week_start_containing(today, start_weekday=start_wd)
    week_start = this_week_start - timedelta(days=7)
    week_end = week_start + timedelta(days=span)
    return week_start, week_end


def format_weekly_followup_period(week_end: date) -> str:
    """由 week_end 生成 period（ISO 年周），如 2026-W20。"""
    iso_year, iso_week, _ = week_end.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def resolve_weekly_followup_week_range_from_period(period: str) -> Tuple[date, date]:
    """
    将 period（如 2026-W20）解析为周区间。

    week_end 取该 ISO 周的「配置的结束星期」；week_start 为结束前 span 天。
    """
    raw = (period or "").strip()
    m = _PERIOD_RE.match(raw)
    if not m:
        raise ValueError(f"无效的 period: {period!r}，期望格式如 2026-W20")

    iso_year = int(m.group(1))
    iso_week = int(m.group(2))
    if iso_week < 1 or iso_week > 53:
        raise ValueError(f"无效的 period 周数: {period!r}")

    start_wd, end_wd = get_weekly_followup_week_boundary()
    iso_end_day = _iso_weekday_from_python(end_wd)
    span = _week_span_days(start_weekday=start_wd, end_weekday=end_wd)

    try:
        week_end = date.fromisocalendar(iso_year, iso_week, iso_end_day)
    except ValueError as e:
        raise ValueError(f"无效的 period: {period!r}") from e

    week_start = week_end - timedelta(days=span)
    return week_start, week_end
