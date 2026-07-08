"""多维表格 DAILY 模式统计窗口计算测试。"""

from datetime import datetime

import pytz

from app.tasks.bitable_import import (
    _bitable_crm_select_sql,
    compute_daily_bitable_window,
    normalize_bitable_record_ids,
    parse_bitable_sync_cron_hour_minute,
)


def test_parse_bitable_sync_cron_hour_minute():
    assert parse_bitable_sync_cron_hour_minute("30 20 * * *") == (20, 30)
    assert parse_bitable_sync_cron_hour_minute("0 13 * * 0") == (13, 0)
    assert parse_bitable_sync_cron_hour_minute("*/5 * * * *") is None


def test_compute_daily_bitable_window_from_cron():
    tz = pytz.timezone("Asia/Shanghai")
    ref = tz.localize(datetime(2026, 6, 15, 20, 35, 0))
    start, end = compute_daily_bitable_window(
        tz,
        cron_expr="30 20 * * *",
        buffer_minutes=30,
        reference=ref,
    )
    assert end == tz.localize(datetime(2026, 6, 15, 20, 0, 0))
    assert start == tz.localize(datetime(2026, 6, 14, 20, 0, 0))


def test_compute_daily_bitable_window_custom_buffer():
    tz = pytz.timezone("Asia/Shanghai")
    ref = tz.localize(datetime(2026, 6, 15, 9, 10, 0))
    start, end = compute_daily_bitable_window(
        tz,
        cron_expr="0 9 * * *",
        buffer_minutes=15,
        reference=ref,
    )
    assert end == tz.localize(datetime(2026, 6, 15, 8, 45, 0))
    assert start == tz.localize(datetime(2026, 6, 14, 8, 45, 0))


def test_normalize_bitable_record_ids():
    assert normalize_bitable_record_ids(["  link_a  ", "link_a", "form_b", ""]) == [
        "link_a",
        "form_b",
    ]
    assert normalize_bitable_record_ids(None) == []


def test_bitable_crm_select_sql_uses_oauth_accounts_for_recorder_open_id():
    sql = _bitable_crm_select_sql("crm_sales_visit_records.record_id IN :record_ids")
    assert "oauth_accounts" in sql
    assert "oa.provider = :oauth_provider" in sql
    assert "up.open_id" not in sql
    assert "recorder_open_id" in sql
    assert "up.department AS recorder_department" in sql
