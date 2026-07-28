"""拜访记录回写窗口与 cron 解析测试。"""

from datetime import date, datetime

import pytz
from celery.schedules import crontab

from app.core.config import WritebackFrequency
from app.services.writeback_window import (
    calendar_day_window,
    datetime_window,
    parse_crontab_fields,
    parse_datetime_in_writeback_tz,
    resolve_writeback_window,
)


def test_parse_crontab_fields_valid():
    assert parse_crontab_fields("*/30 * * * *") == ("*/30", "*", "*", "*", "*")
    assert parse_crontab_fields("0 14 * * 0") == ("0", "14", "*", "*", "0")
    assert parse_crontab_fields("  15 9 * * 1-5  ") == ("15", "9", "*", "*", "1-5")


def test_parse_crontab_fields_invalid():
    assert parse_crontab_fields("") is None
    assert parse_crontab_fields("0 14 * *") is None
    assert parse_crontab_fields("*/30 * * * * extra") is None


def test_celery_crontab_accepts_star_slash_30():
    """Celery crontab 能接受 */30 分钟表达式（分贝通高频调度）。"""
    fields = parse_crontab_fields("*/30 * * * *")
    assert fields is not None
    minute, hour, day_of_month, month_of_year, day_of_week = fields
    schedule = crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )
    assert schedule is not None


def test_resolve_writeback_window_daily():
    tz = pytz.timezone("Asia/Shanghai")
    now = tz.localize(datetime(2026, 6, 15, 14, 0, 0))
    window = resolve_writeback_window(
        tz, WritebackFrequency.DAILY, now=now
    )
    assert window.mode == "calendar"
    assert window.frequency == WritebackFrequency.DAILY
    assert window.start_local.date() == date(2026, 6, 14)
    assert window.end_local.date() == date(2026, 6, 14)
    assert window.start_local.hour == 0
    assert window.end_local.hour == 23
    # Asia/Shanghai = UTC+8
    assert window.start_utc == datetime(2026, 6, 13, 16, 0, 0)


def test_resolve_writeback_window_weekly():
    tz = pytz.timezone("Asia/Shanghai")
    # 2026-06-15 是周一
    now = tz.localize(datetime(2026, 6, 15, 10, 0, 0))
    window = resolve_writeback_window(
        tz, WritebackFrequency.WEEKLY, now=now
    )
    assert window.mode == "calendar"
    assert window.frequency == WritebackFrequency.WEEKLY
    assert window.start_local.date() == date(2026, 6, 7)  # 上周日
    assert window.end_local.date() == date(2026, 6, 13)  # 本周六


def test_resolve_writeback_window_interval():
    tz = pytz.timezone("Asia/Shanghai")
    now = tz.localize(datetime(2026, 6, 15, 14, 30, 0))
    window = resolve_writeback_window(
        tz,
        WritebackFrequency.INTERVAL,
        lookback_minutes=35,
        now=now,
    )
    assert window.mode == "datetime"
    assert window.frequency == WritebackFrequency.INTERVAL
    assert window.end_local == now
    assert window.start_local == tz.localize(datetime(2026, 6, 15, 13, 55, 0))
    assert window.start_utc == datetime(2026, 6, 15, 5, 55, 0)
    assert window.end_utc == datetime(2026, 6, 15, 6, 30, 0)


def test_resolve_writeback_window_interval_rejects_non_positive_lookback():
    tz = pytz.timezone("Asia/Shanghai")
    now = tz.localize(datetime(2026, 6, 15, 14, 0, 0))
    try:
        resolve_writeback_window(
            tz,
            WritebackFrequency.INTERVAL,
            lookback_minutes=0,
            now=now,
        )
        assert False, "expected ValueError"
    except ValueError as e:
        assert "LOOKBACK" in str(e) or "lookback" in str(e).lower()


def test_calendar_day_window_and_datetime_window():
    tz = pytz.timezone("Asia/Shanghai")
    cal = calendar_day_window(date(2026, 6, 1), date(2026, 6, 2), tz)
    assert cal.mode == "calendar"
    assert cal.start_utc == datetime(2026, 5, 31, 16, 0, 0)

    start = tz.localize(datetime(2026, 6, 15, 10, 0, 0))
    end = tz.localize(datetime(2026, 6, 15, 10, 30, 0))
    dt_win = datetime_window(start, end, tz)
    assert dt_win.mode == "datetime"
    assert dt_win.start_utc == datetime(2026, 6, 15, 2, 0, 0)
    assert dt_win.end_utc == datetime(2026, 6, 15, 2, 30, 0)


def test_parse_datetime_in_writeback_tz():
    tz = pytz.timezone("Asia/Shanghai")
    local = parse_datetime_in_writeback_tz("2026-06-15 14:30:00", tz)
    assert local == tz.localize(datetime(2026, 6, 15, 14, 30, 0))

    utc = parse_datetime_in_writeback_tz("2026-06-15T06:30:00Z", tz)
    assert utc == tz.localize(datetime(2026, 6, 15, 14, 30, 0))
