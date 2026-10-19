"""一线销售今日重点：组装过滤、本人查询、推送跳过。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.api.routes.notification_schemas import SalesTodayHighlightsPushRequest
from app.services.report_push_policy import parse_report_push_policy
from app.services.sales_today_highlights import (
    assemble_sales_highlights,
    card_action_buttons,
    card_blue_links,
    deliver_highlight_card,
    handle_sales_today_highlights,
    highlights_for_viewer,
    load_sales_extract_rows,
    render_card_links,
    render_card_markdown,
    week_followup_link,
    week_task_summary_link,
)
from app.services.sales_week_visits import summarize_week_visit_recaps

MONDAY = date(2026, 10, 12)
FRIDAY = date(2026, 10, 9)
THURSDAY = date(2026, 10, 8)


def _row(
    item_type,
    payload,
    *,
    subject_id="oauth-1",
    card_links=None,
    unique_id="u1",
    account_id=None,
    opportunity_id=None,
    subject_type="USER",
    audience="sales",
    status="PENDING",
    extract_key=None,
    created_at=None,
    feedback=None,
    feedback_comment=None,
    adopted_todo_id=None,
):
    return {
        "unique_id": unique_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "audience": audience,
        "status": status,
        "is_deleted": 0,
        "item_type": item_type,
        "extract_key": unique_id if extract_key is None else extract_key,
        "account_id": account_id,
        "opportunity_id": opportunity_id,
        "card_links": card_links or [],
        "payload": payload,
        "created_at": created_at,
        "feedback": feedback,
        "feedback_comment": feedback_comment,
        "adopted_todo_id": adopted_todo_id,
        "reject_reason": None,
        "feedback_at": None,
    }


def _names():
    return {
        "accounts": {"acc-1": "甲客户"},
        "opportunities": {"opp-1": "乙商机"},
    }


def test_schema_defaults():
    payload = SalesTodayHighlightsPushRequest()
    assert payload.type == "sales_today_highlights"
    assert payload.report_date is None
    assert payload.recipient_user_ids is None


def test_thursday_drops_next_week_and_unchanged_risk_keeps_resolved(monkeypatch):
    monkeypatch.setattr(
        "app.services.sales_today_highlights.build_sales_today_highlights_url",
        lambda report_date, section: f"https://example.com/v2/today-highlights?date={report_date}&tab={section}",
    )
    rows = [
        _row(
            "IMMEDIATE_ACTION",
            {"claim": "确认排期", "why_today": "今天不定就延期", "confidence": 0.9},
            unique_id="imm",
            account_id="acc-1",
            opportunity_id="opp-1",
            card_links=[{"key": "immediate_today", "title": "立即处理"}],
        ),
        _row(
            "OPEN_RISK",
            {"claim": "没变化", "risk_change": "unchanged", "risk_status": "active", "confidence": 0.8},
            unique_id="same",
        ),
        _row(
            "OPEN_RISK",
            {"claim": "窗口关掉了", "risk_change": "resolved", "risk_status": "resolved", "confidence": 0.4},
            unique_id="closed",
            card_links=[{"key": "open_risks", "title": "商机风险"}],
        ),
        _row(
            "NEXT_WEEK_ACTION_CANDIDATE",
            {"claim": "下周拜访", "confirm_prompt": "这件下周还做吗", "coverage": "partial", "confidence": 0.7},
            unique_id="next",
            card_links=[{"key": "next_week_confirm", "title": "下周待确认"}],
        ),
        _row(
            "OPEN_RISK",
            {"claim": "部门风险", "risk_change": "new"},
            unique_id="dept",
            subject_type="DEPARTMENT",
            audience="leader",
        ),
    ]
    thursday = assemble_sales_highlights(rows, THURSDAY, _names())
    keys = [section["key"] for section in thursday["sections"]]
    assert keys == ["immediate_today", "open_risks"]
    risk_claims = [item["claim"] for item in thursday["sections"][1]["items"]]
    assert risk_claims == ["窗口关掉了"]
    body = render_card_markdown(thursday)
    assert "确认排期" in body
    assert "甲客户" in body and "乙商机" in body
    assert "已闭环" not in body
    assert "这件下周还做吗" not in body
    assert [label for label, _url in card_blue_links(thursday)] == ["商机风险"]

    friday = assemble_sales_highlights(rows, FRIDAY, _names())
    friday_keys = [section["key"] for section in friday["sections"]]
    assert friday_keys == ["immediate_today", "open_risks", "next_week_confirm"]
    assert friday["sections"][2]["items"][0]["confirm_prompt"] == "这件下周还做吗"
    assert "这件下周还做吗" not in render_card_markdown(friday)
    assert [label for label, _url in card_blue_links(friday)] == ["商机风险", "下周待确认"]


def test_immediate_item_carries_decision_and_feedback():
    assembled = assemble_sales_highlights(
        [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "确认排期", "why_today": "今天不定就延期"},
                status="ADOPTED",
                feedback="up",
                feedback_comment="判断准确",
                adopted_todo_id="todo-1",
            )
        ],
        THURSDAY,
        _names(),
    )
    item = assembled["sections"][0]["items"][0]
    assert item["status"] == "ADOPTED"
    assert item["feedback"] == "up"
    assert item["feedback_comment"] == "判断准确"
    assert item["adopted_todo_id"] == "todo-1"
    risk = assemble_sales_highlights(
        [
            _row(
                "OPEN_RISK",
                {"claim": "窗口关掉了", "risk_change": "resolved"},
                unique_id="risk-1",
                status="REJECTED",
                feedback="down",
            )
        ],
        THURSDAY,
        _names(),
    )
    risk_item = risk["sections"][0]["items"][0]
    assert risk_item["status"] == "REJECTED"
    assert risk_item["feedback"] == "down"


def test_reextract_keeps_the_decided_immediate_row():
    assembled = assemble_sales_highlights(
        [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "旧建议", "why_today": "昨天"},
                unique_id="old",
                extract_key="same",
                status="ADOPTED",
                adopted_todo_id="todo-1",
                feedback="down",
                created_at="2026-10-08T08:00:00",
            ),
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "新建议", "why_today": "今天"},
                unique_id="new",
                extract_key="same",
                status="PENDING",
                created_at="2026-10-08T10:00:00",
            ),
        ],
        THURSDAY,
        _names(),
    )
    items = assembled["sections"][0]["items"]
    assert len(items) == 1
    assert items[0]["unique_id"] == "old"
    assert items[0]["claim"] == "新建议"
    assert items[0]["why_today"] == "今天"
    assert items[0]["status"] == "ADOPTED"
    assert items[0]["adopted_todo_id"] == "todo-1"
    assert items[0]["feedback"] == "down"


def test_card_shows_top_immediate_and_blue_links_for_the_rest(monkeypatch):
    monkeypatch.setattr(
        "app.services.sales_today_highlights.build_sales_today_highlights_url",
        lambda report_date, section: f"https://example.com/v2/today-highlights?date={report_date}&tab={section}",
    )
    risks = [
        _row(
            "OPEN_RISK",
            {"claim": f"风险{index}", "risk_change": "new", "confidence": index / 10},
            unique_id=f"r{index}",
            card_links=[{"key": "open_risks", "title": "自定义风险"}],
        )
        for index in range(1, 5)
    ]
    immediates = [
        _row(
            "IMMEDIATE_ACTION",
            {"claim": "稍后处理", "why_today": "不急", "confidence": 0.2},
            unique_id="later",
            card_links=[{"key": "immediate_today", "title": "立即处理"}],
        ),
        _row(
            "IMMEDIATE_ACTION",
            {"claim": "先做这个", "why_today": "今天不定就延期", "confidence": 0.9},
            unique_id="now",
            account_id="acc-1",
            opportunity_id="opp-1",
            card_links=[{"key": "immediate_today", "title": "立即处理"}],
        ),
    ]
    assembled = assemble_sales_highlights([*immediates, *risks], THURSDAY, _names())
    body = render_card_markdown(assembled)
    assert "先做这个" in body
    assert "稍后处理" not in body
    assert "风险1" not in body
    assert "还有" not in body
    assert "**立即处理**" in body
    assert "[立即处理]" not in body
    assert "[更多（1）](https://example.com/v2/today-highlights?date=2026-10-08&tab=immediate_today)" in body
    assert body.rstrip().endswith(
        "[更多（1）](https://example.com/v2/today-highlights?date=2026-10-08&tab=immediate_today)"
    )
    links = card_blue_links(assembled)
    assert links == [
        ("自定义风险（4）", "https://example.com/v2/today-highlights?date=2026-10-08&tab=open_risks"),
    ]
    assert "商机风险" not in render_card_links(assembled)
    assert len(assembled["sections"][1]["items"]) == 4

    only = assemble_sales_highlights(
        [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "只有这一条", "why_today": "今天要定", "confidence": 0.8},
                unique_id="solo",
                card_links=[{"key": "immediate_today", "title": "立即处理"}],
            )
        ],
        THURSDAY,
        _names(),
    )
    solo_body = render_card_markdown(only)
    assert "更多" not in solo_body
    assert "[立即处理]" not in solo_body
    assert "**立即处理**" in solo_body
    assert (
        "[查看](https://example.com/v2/today-highlights?date=2026-10-08&tab=immediate_today)"
        in solo_body
    )


def test_dingtalk_card_puts_title_in_the_body(monkeypatch):
    captured = {}

    def _send(db_session, **kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.send_platform_notification",
        _send,
    )
    assembled = assemble_sales_highlights(
        [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "先做这个", "why_today": "今天要定", "confidence": 0.9},
                card_links=[{"key": "immediate_today", "title": "立即处理"}],
            )
        ],
        THURSDAY,
    )
    deliver_highlight_card(
        MagicMock(),
        {"platform": "dingtalk", "user_id": "user-1", "name": "张三"},
        assembled,
    )
    content = captured["content"]
    assert content.startswith("# 今日重点｜2026-10-08\n")
    assert "## 立即处理\n" in content
    assert "> 今天要定" in content
    assert "[查看](" in content
    assert "\n\n\n[本周待完成]" in content
    assert "- [本周待完成]" not in content


def test_week_task_summary_link_filters_own_incomplete_due_this_week(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.CRM_SALES_TASK_PAGE_URL",
        "/v2/task",
    )
    link = week_task_summary_link("张三", THURSDAY)
    assert link is not None
    label, url = link
    assert label == "本周待完成"
    assert url.startswith("https://example.com/v2/task?")
    assert "owner_name=" in url
    assert "due_date__gte=2026-10-05" in url
    assert "due_date__lte=2026-10-11" in url
    assert "is_overdue" not in url
    assert "ai_status__in" not in url
    assert "status__in=IN_PROGRESS" in url
    assert "status__in=PENDING" in url
    assert week_task_summary_link("", THURSDAY) is None
    assert week_task_summary_link("Unknown", THURSDAY) is None

    assembled = assemble_sales_highlights(
        [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "先做这个", "confidence": 0.9},
                card_links=[{"key": "immediate_today", "title": "立即处理"}],
            )
        ],
        THURSDAY,
    )
    assert [label for label, _url in card_blue_links(assembled)] == []
    assert [label for label, _url in card_action_buttons(assembled, owner_name="张三")] == [
        "本周待完成",
        "本周已拜访",
    ]
    friday = assemble_sales_highlights(
        [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "先做这个", "confidence": 0.9},
                card_links=[{"key": "immediate_today", "title": "立即处理"}],
            )
        ],
        FRIDAY,
    )
    assert [label for label, _url in card_blue_links(friday)] == []
    assert [label for label, _url in card_action_buttons(friday, owner_name="张三")] == [
        "本周待完成",
        "本周已拜访",
    ]
    assert "本周待完成" not in render_card_links(friday)
    assert week_followup_link("张三", MONDAY) is None
    assert week_followup_link("张三", THURSDAY) is not None
    assert week_followup_link("", FRIDAY) is None
    assert week_followup_link("Unknown", FRIDAY) is None


def test_week_followup_link_opens_own_visit_list(monkeypatch):
    from app.core.config import CRMDailyReportVisitDateField

    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.VISIT_DETAIL_PAGE_URL",
        "/v2/behavior",
    )
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.CRM_DAILY_REPORT_VISIT_DATE_FIELD",
        CRMDailyReportVisitDateField.VISIT_COMMUNICATION_DATE,
    )
    link = week_followup_link("张三", FRIDAY)
    assert link is not None
    label, url = link
    assert label == "本周已拜访"
    assert url == (
        "https://example.com/v2/behavior"
        "?visit_communication_date_start=2026-10-05"
        "&visit_communication_date_end=2026-10-11"
        "&recorder=%E5%BC%A0%E4%B8%89"
    )

    summary = summarize_week_visit_recaps(
        ["rec-1", "rec-2", "rec-3", ""],
        [
            {"entity_id": "rec-1", "severity": "high"},
            {"entity_id": "rec-1", "severity": "low"},
            {"entity_id": "rec-2", "severity": "MEDIUM"},
            {"entity_id": "other", "severity": "HIGH"},
        ],
    )
    assert summary["visit_total"] == 4
    assert summary["recap_total"] == 2
    assert summary["recap_by_severity"] == [
        {"severity": "HIGH", "count": 1},
        {"severity": "MEDIUM", "count": 1},
        {"severity": "LOW", "count": 0},
    ]


def test_empty_sections_have_no_content():
    rows = [
        _row(
            "OPEN_RISK",
            {"claim": "没变化", "risk_change": "unchanged"},
            unique_id="same",
        )
    ]
    assembled = assemble_sales_highlights(rows, THURSDAY)
    assert assembled["has_content"] is False
    assert render_card_markdown(assembled) == ""


def test_viewer_query_uses_own_oauth_id(monkeypatch):
    seen = {}

    def fake_load(session, report_date, oauth_user_ids=None):
        seen["ids"] = list(oauth_user_ids or [])
        seen["date"] = report_date
        return [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "只属于我", "why_today": "今天"},
                subject_id="oauth-me",
                card_links=[{"key": "immediate_today", "title": "立即处理"}],
            )
        ]

    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_display_names",
        lambda session, rows: {"accounts": {}, "opportunities": {}},
    )
    data = highlights_for_viewer(MagicMock(), "oauth-me", FRIDAY)
    assert seen["ids"] == ["oauth-me"]
    assert data["subject_id"] == "oauth-me"
    assert data["items"]["immediate_today"][0]["claim"] == "只属于我"
    assert "oauth-other" not in str(data)


def test_load_filters_subject_when_ids_given():
    session = MagicMock()
    captured = {}

    def exec_(statement):
        captured["statement"] = statement
        result = MagicMock()
        result.all.return_value = []
        return result

    session.exec.side_effect = exec_
    load_sales_extract_rows(session, FRIDAY, oauth_user_ids=["oauth-me"])
    compiled = captured["statement"].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "subject_id" in sql
    assert "oauth-me" in sql


def test_variant_disabled_skips_without_reading_rows(monkeypatch):
    called = {"load": 0}

    def fake_load(*args, **kwargs):
        called["load"] += 1
        return []

    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": False}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        fake_load,
    )
    result = handle_sales_today_highlights(MagicMock(), report_date=FRIDAY)
    assert result["skipped"] is True
    assert result["skip_reason"] == "variant_disabled"
    assert called["load"] == 0


def test_empty_day_skips_without_sending(monkeypatch):
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: [
            _row("OPEN_RISK", {"claim": "没变化", "risk_change": "unchanged"}, unique_id="same")
        ],
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_display_names",
        lambda *args, **kwargs: {"accounts": {}, "opportunities": {}},
    )
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.sales_today_highlights.deliver_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_sales_today_highlights(MagicMock(), report_date=THURSDAY)
    assert result["skipped"] is True
    assert result["skip_reason"] == "empty_highlights"
    assert sent["count"] == 0
    assert "今天没有重点" not in str(result)


def test_opt_out_skips_send(monkeypatch):
    profile = SimpleNamespace(
        is_active=True,
        user_id=uuid4(),
        name="张三",
        department="销售",
        oauth_user=SimpleNamespace(open_id="ou_1", provider="feishu"),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "今天打电话", "why_today": "承诺今天"},
                card_links=[{"key": "immediate_today", "title": "立即处理"}],
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_display_names",
        lambda *args, **kwargs: {"accounts": {}, "opportunities": {}},
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: profile,
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: [],
    )
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.sales_today_highlights.deliver_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_sales_today_highlights(MagicMock(), report_date=THURSDAY)
    assert result["skip_reason"] == "opted_out"
    assert sent["count"] == 0


def test_oauth_id_resolves_open_id_and_uses_snapshot_title(monkeypatch):
    profile = SimpleNamespace(
        is_active=True,
        user_id=uuid4(),
        name="张三",
        department="销售",
        oauth_user=SimpleNamespace(open_id="ou_1", provider="feishu"),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.build_sales_today_highlights_url",
        lambda report_date, section: f"https://example.com/today?tab={section}",
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: [
            _row(
                "IMMEDIATE_ACTION",
                {"claim": "今天打电话", "why_today": "承诺今天"},
                card_links=[{"key": "immediate_today", "title": "立即处理"}],
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_display_names",
        lambda *args, **kwargs: {"accounts": {}, "opportunities": {}},
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: profile if oauth_id == "oauth-1" else None,
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    captured = {}

    def fake_deliver(session, recipient, assembled):
        captured["recipient"] = recipient
        captured["links"] = card_blue_links(assembled)
        captured["body"] = render_card_markdown(assembled)
        return {"success": True}

    monkeypatch.setattr(
        "app.services.sales_today_highlights.deliver_highlight_card",
        fake_deliver,
    )
    result = handle_sales_today_highlights(
        MagicMock(),
        report_date="2026-10-08",
        recipient_user_ids=["oauth-1"],
    )
    assert result["success"] is True
    assert result["success_count"] == 1
    assert captured["recipient"]["open_id"] == "ou_1"
    assert captured["recipient"]["user_id"] == str(profile.user_id)
    assert captured["links"] == []
    assert "立即处理" in captured["body"]
    assert "今天打电话" in captured["body"]
    assert "商机风险" not in captured["body"]


def test_missing_open_id_skips(monkeypatch):
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: [
            _row("IMMEDIATE_ACTION", {"claim": "今天打电话", "why_today": "承诺今天"})
        ],
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_display_names",
        lambda *args, **kwargs: {"accounts": {}, "opportunities": {}},
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: None,
    )
    result = handle_sales_today_highlights(MagicMock(), report_date=THURSDAY)
    assert result["skip_reason"] == "no_open_id"
    assert result["success_count"] == 0


def test_invalid_report_date():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        handle_sales_today_highlights(MagicMock(), report_date="10/08")


def test_highlights_task_runs_apart_from_daily_report(monkeypatch):
    from app.tasks.cron_jobs import push_crm_today_highlights

    class _Session:
        def __enter__(self):
            return MagicMock()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.tasks.cron_jobs.Session", lambda engine: _Session())
    seen = {}

    def fake_sales(session, report_date=None, recipient_user_ids=None):
        seen["sales"] = report_date
        return {"success": True, "skipped": False, "skip_reason": None, "success_count": 1}

    def fake_department(session, *, report_date=None, department_id=None):
        seen["department"] = report_date
        return {"success": True, "skipped": False, "skip_reason": None, "success_count": 2}

    monkeypatch.setattr(
        "app.services.sales_today_highlights.handle_sales_today_highlights",
        fake_sales,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.handle_department_today_highlights",
        fake_department,
    )
    result = push_crm_today_highlights(report_date_str="2026-10-08")
    assert seen["sales"] == date(2026, 10, 8)
    assert seen["department"] == date(2026, 10, 8)
    assert result["sales_today_highlights"]["success_count"] == 1
    assert result["department_today_highlights"]["success_count"] == 2

    seen.clear()
    result = push_crm_today_highlights()
    assert seen["sales"] is None
    assert seen["department"] is None
    assert result["report_date"] is None

    seen.clear()
    result = push_crm_today_highlights(report_date_str="2026-10-08", audience="sales")
    assert seen == {"sales": date(2026, 10, 8)}
    assert result["department_today_highlights"] is None

    bad = push_crm_today_highlights(report_date_str="10/08")
    assert bad["success"] is False
    assert bad["sales_today_highlights"] is None
    assert bad["department_today_highlights"] is None
