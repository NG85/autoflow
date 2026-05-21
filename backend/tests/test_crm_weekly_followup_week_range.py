from datetime import date, timedelta

import pytest

from app.core.config import CRMWeeklyFollowupWeekPreset, settings
from app.utils.crm_weekly_followup_week_boundary import (
    format_weekly_followup_period,
    resolve_weekly_followup_week_range,
    resolve_weekly_followup_week_range_from_period,
)


def test_completed_week_sat_fri_default():
    # 2025-12-27 周六 -> 上一完整周 2025-12-20 ~ 2025-12-26
    start, end = resolve_weekly_followup_week_range(date(2025, 12, 27), week_range_mode="completed")
    assert start == date(2025, 12, 20)
    assert end == date(2025, 12, 26)


def test_completed_week_mon_sun_preset(monkeypatch):
    monkeypatch.setattr(settings, "CRM_WEEKLY_FOLLOWUP_WEEK_PRESET", CRMWeeklyFollowupWeekPreset.MON_SUN)
    monkeypatch.setattr(settings, "CRM_WEEKLY_FOLLOWUP_WEEK_START_WEEKDAY", None)
    monkeypatch.setattr(settings, "CRM_WEEKLY_FOLLOWUP_WEEK_END_WEEKDAY", None)
    # 2025-12-31 周三 -> 上一完整周一~周日 2025-12-22 ~ 2025-12-28
    start, end = resolve_weekly_followup_week_range(date(2025, 12, 31), week_range_mode="completed")
    assert start == date(2025, 12, 22)
    assert end == date(2025, 12, 28)


def test_completed_week_custom_weekdays(monkeypatch):
    monkeypatch.setattr(settings, "CRM_WEEKLY_FOLLOWUP_WEEK_START_WEEKDAY", 6)
    monkeypatch.setattr(settings, "CRM_WEEKLY_FOLLOWUP_WEEK_END_WEEKDAY", 5)
    # sun_sat：2025-12-28 周日 -> 2025-12-21 ~ 2025-12-27
    start, end = resolve_weekly_followup_week_range(date(2025, 12, 28), week_range_mode="completed")
    assert start == date(2025, 12, 21)
    assert end == date(2025, 12, 27)


def test_in_progress_week_on_wednesday():
    start, end = resolve_weekly_followup_week_range(date(2025, 12, 24), week_range_mode="in_progress")
    assert start == date(2025, 12, 20)
    assert end == date(2025, 12, 24)


def test_period_2026_w20():
    start, end = resolve_weekly_followup_week_range_from_period("2026-W20")
    assert end == date.fromisocalendar(2026, 20, 5)
    assert start == end - timedelta(days=6)
    assert format_weekly_followup_period(end) == "2026-W20"


def test_period_invalid():
    with pytest.raises(ValueError):
        resolve_weekly_followup_week_range_from_period("2026-W99")
    with pytest.raises(ValueError):
        resolve_weekly_followup_week_range_from_period("bad-period")
