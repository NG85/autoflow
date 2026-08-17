"""多维表格 DAILY 模式统计窗口计算测试。"""

from datetime import datetime

import pytz

from app.tasks.bitable_import import (
    _bitable_crm_select_sql,
    build_bitable_fields_from_crm_row,
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
    assert "crm_sales_visit_records.followup_object_type" in sql
    assert "crm_sales_visit_records.followup_object_id" in sql
    assert "crm_sales_visit_records.followup_object_name" in sql


def test_build_bitable_fields_writes_lead_name_not_account_name():
    fields = build_bitable_fields_from_crm_row(
        {
            "record_id": "form_lead_001",
            "account_name": "应忽略的双写客户名",
            "followup_object_type": "lead",
            "followup_object_id": "5ff3e47230bbb7000193f7e7",
            "followup_object_name": "测试线索",
            "opportunity_name": None,
        }
    )
    assert fields["线索"] == "测试线索"
    assert fields["唯一ID"] == "form_lead_001"
    assert "客户名称" not in fields


def test_build_bitable_fields_lead_falls_back_to_id():
    fields = build_bitable_fields_from_crm_row(
        {
            "record_id": "form_lead_002",
            "followup_object_type": "lead",
            "followup_object_id": "5ff3e47230bbb7000193f7e7",
            "followup_object_name": None,
        }
    )
    assert fields["线索"] == "5ff3e47230bbb7000193f7e7"


def test_build_bitable_fields_does_not_write_lead_for_customer():
    fields = build_bitable_fields_from_crm_row(
        {
            "record_id": "form_acc_001",
            "account_name": "测试客户",
            "followup_object_type": "end_customer",
            "followup_object_id": "acc_001",
            "followup_object_name": "测试客户",
        }
    )
    assert fields["客户名称"] == "测试客户"
    assert "线索" not in fields
