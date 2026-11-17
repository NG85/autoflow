"""CXO 今日重点：发卡展示公司人效和重点商机盘点。"""

from datetime import date
from unittest.mock import MagicMock

from app.services.company_today_highlights import (
    COMPANY_SCOPE_NAME,
    handle_company_today_highlights,
    highlights_for_viewer,
    insight_period_key,
    pick_report_sections,
    render_card_markdown,
    render_dingtalk_markdown,
)
from app.services.report_push_policy import parse_report_push_policy
from app.services.today_highlights_wecom import build_cxo_wecom_template_card

MONDAY = date(2026, 11, 9)
TUESDAY = date(2026, 11, 10)
WEDNESDAY = date(2026, 11, 11)
PERIOD = "2026-11-09"


def _row(
    insight_type,
    entity_id,
    *,
    period_key=PERIOD,
    summary="",
    detail="正文",
    generated_at="2026-11-09T08:00:00",
    row_id=1,
    entity_type="DEPARTMENT",
):
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "insight_type": insight_type,
        "period_key": period_key,
        "summary": summary,
        "detail_text": detail,
        "unique_id": f"{insight_type}-{entity_id}-{row_id}",
        "generated_at": generated_at,
        "id": row_id,
    }


def _names():
    return {"dept-east": "华东", "dept-north": "华北"}


def _policy(*user_ids, enabled=True):
    return parse_report_push_policy(
        {
            "company_highlights": {
                "today_highlights": {
                    "enabled": enabled,
                    "recipient_user_ids": list(user_ids),
                }
            }
        }
    )


def _enable(monkeypatch, *user_ids, enabled=True):
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_report_push_policy",
        lambda: _policy(*user_ids, enabled=enabled),
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.check_today_highlights_quota",
        lambda audience: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.build_company_highlights_url",
        lambda day, section="": f"https://example.com/cxo?date={day}&tab={section}"
        if section
        else f"https://example.com/cxo?date={day}",
    )


def test_period_key_is_the_push_day():
    assert insight_period_key(MONDAY) == MONDAY.isoformat()
    assert insight_period_key(WEDNESDAY) == WEDNESDAY.isoformat()
    assert insight_period_key(date(2026, 11, 13)) == "2026-11-13"


def test_sections_keep_company_first_and_drop_empty_or_other_types(monkeypatch):
    monkeypatch.setattr(
        "app.services.company_today_highlights.build_company_highlights_url",
        lambda day, section="": f"https://example.com/cxo?date={day}&tab={section}",
    )
    rows = [
        _row("CXO_EFFICIENCY_INSIGHT", "dept-north", detail="华北人效", row_id=1),
        _row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__", detail="公司人效", row_id=2),
        _row("CXO_EFFICIENCY_INSIGHT", "dept-east", detail="华东旧", row_id=3, generated_at="2026-11-09T07:00:00"),
        _row("CXO_EFFICIENCY_INSIGHT", "dept-east", detail="华东人效", row_id=4, generated_at="2026-11-09T09:00:00"),
        _row("CXO_KEY_DEAL_REVIEW", "dept-east", detail="华东商机", row_id=5),
        _row("CXO_EFFICIENCY_INSIGHT", "dept-east", detail="", summary="", row_id=6),
        _row("LEADER_EFFICIENCY_INSIGHT", "dept-east", detail="主管人效", row_id=7),
        _row("CXO_EFFICIENCY_INSIGHT", "user-1", detail="不该出现", row_id=8, entity_type="USER"),
        _row("CXO_KEY_DEAL_REVIEW", "dept-east", detail="昨天的", period_key="2026-11-08", row_id=9),
    ]
    sections = pick_report_sections(rows, push_day=MONDAY, names=_names())
    by_key = {section["key"]: section for section in sections}
    efficiency = [item["department_name"] for item in by_key["cxo_efficiency_insight"]["items"]]
    assert efficiency == [COMPANY_SCOPE_NAME, "华东", "华北"]
    assert by_key["cxo_efficiency_insight"]["items"][1]["detail_text"] == "华东人效"
    assert by_key["cxo_key_deal_review"]["title"] == "重点商机盘点"
    assert by_key["cxo_efficiency_insight"]["url"].endswith("tab=cxo_efficiency_insight")
    assert "不该出现" not in render_card_markdown({"report_entries": []})


def test_wednesday_card_omits_key_deals_when_that_insight_is_absent(monkeypatch):
    monkeypatch.setattr(
        "app.services.company_today_highlights.build_company_highlights_url",
        lambda day, section="": f"https://example.com/cxo?date={day}&tab={section}",
    )
    rows = [_row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__", detail="周三人效", period_key=WEDNESDAY.isoformat())]
    sections = pick_report_sections(rows, push_day=WEDNESDAY, names={})
    assert [section["title"] for section in sections] == ["公司人效"]
    assembled = {
        "report_date": WEDNESDAY.isoformat(),
        "report_entries": [
            {"key": "cxo_efficiency_insight", "label": "公司人效", "url": "https://example.com/eff"}
        ],
    }
    body = render_card_markdown(assembled)
    assert body == "[公司人效](https://example.com/eff)"
    assert "重点商机盘点" not in body
    dingtalk = render_dingtalk_markdown(assembled, title="CXO今日重点｜2026-11-11")
    assert dingtalk.startswith("# CXO今日重点｜2026-11-11")


def test_wecom_card_puts_both_reports_in_jump_list():
    card = build_cxo_wecom_template_card(
        {
            "report_date": MONDAY.isoformat(),
            "report_entries": [
                {"label": "公司人效", "url": "https://example.com/eff"},
                {"label": "重点商机盘点", "url": "https://example.com/deal"},
            ],
        }
    )
    assert card["main_title"]["title"] == "CXO今日重点｜2026-11-09"
    assert [item["title"] for item in card["jump_list"]] == ["公司人效", "重点商机盘点"]
    assert card["card_action"]["url"] == "https://example.com/eff"
    assert "horizontal_content_list" not in card


def test_tuesday_is_not_rejected_for_weekday(monkeypatch):
    _enable(monkeypatch, "user-1", enabled=False)
    result = handle_company_today_highlights(MagicMock(), report_date=TUESDAY)
    assert result["skip_reason"] == "variant_disabled"


def test_variant_and_billing_and_empty_skip(monkeypatch):
    _enable(monkeypatch, "user-1", enabled=False)
    disabled = handle_company_today_highlights(MagicMock(), report_date=MONDAY)
    assert disabled["skip_reason"] == "variant_disabled"

    _enable(monkeypatch, "user-1")
    monkeypatch.setattr(
        "app.services.company_today_highlights.check_today_highlights_quota",
        lambda audience: (False, "额度不足"),
    )
    blocked = handle_company_today_highlights(MagicMock(), report_date=MONDAY)
    assert blocked["skip_reason"] == "billing_blocked"

    _enable(monkeypatch, "user-1")
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        lambda *args, **kwargs: [],
    )
    empty = handle_company_today_highlights(MagicMock(), report_date=MONDAY)
    assert empty["skip_reason"] == "empty_highlights"


def test_monday_sends_one_card_to_named_recipients(monkeypatch):
    sent = []
    billed = []
    _enable(monkeypatch, "user-1", "user-2")
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        lambda *args, **kwargs: [
            _row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__", detail="公司人效"),
            _row("CXO_KEY_DEAL_REVIEW", "dept-east", detail="华东商机"),
        ],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_department_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.recipients_from_user_ids",
        lambda session, ids, recipient_type="named_recipient": [
            {
                "user_id": uid,
                "open_id": f"ou_{uid}",
                "platform": "feishu",
                "name": "CXO",
                "receive_id_type": "open_id",
            }
            for uid in ids
        ],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )

    def fake_send(session, recipient, assembled):
        sent.append((recipient["user_id"], render_card_markdown(assembled)))
        return {"success": True}

    monkeypatch.setattr(
        "app.services.company_today_highlights.deliver_company_highlight_card",
        fake_send,
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.report_today_highlights_usage",
        lambda audience, **kwargs: billed.append((audience, kwargs["user_id"])),
    )
    result = handle_company_today_highlights(MagicMock(), report_date=MONDAY)
    assert result["success_count"] == 2
    assert result["sent_user_ids"] == ["user-1", "user-2"]
    assert sent[0][1] == sent[1][1]
    assert "[公司人效](" in sent[0][1]
    assert "[重点商机盘点](" in sent[0][1]
    assert sent[0][1].index("公司人效") < sent[0][1].index("重点商机盘点")
    assert billed == [("company", "user-1"), ("company", "user-2")]


def test_recipient_subset_cannot_add_people_outside_the_policy(monkeypatch):
    seen = {}
    _enable(monkeypatch, "user-1")
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        lambda *args, **kwargs: [_row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__")],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_department_names",
        lambda *args, **kwargs: {},
    )

    def fake_recipients(session, ids, recipient_type="named_recipient"):
        seen["ids"] = list(ids)
        return []

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.recipients_from_user_ids",
        fake_recipients,
    )
    outsider = handle_company_today_highlights(
        MagicMock(),
        report_date=MONDAY,
        recipient_user_ids=["someone-else"],
    )
    assert outsider["skip_reason"] == "no_recipients"
    assert "ids" not in seen

    missing_open_id = handle_company_today_highlights(MagicMock(), report_date=MONDAY)
    assert missing_open_id["skip_reason"] == "no_open_id"
    assert seen["ids"] == ["user-1"]


def test_opt_out_skips_without_sending(monkeypatch):
    _enable(monkeypatch, "user-1")
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        lambda *args, **kwargs: [_row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__")],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_department_names",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.recipients_from_user_ids",
        lambda session, ids, recipient_type="named_recipient": [
            {"user_id": "user-1", "open_id": "ou_1", "platform": "feishu", "name": "CXO"}
        ],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: [],
    )
    result = handle_company_today_highlights(MagicMock(), report_date=MONDAY)
    assert result["skip_reason"] == "opted_out"
    assert result["success_count"] == 0


def test_unconfigured_list_uses_company_daily_recipients(monkeypatch):
    sent = []
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        lambda *args, **kwargs: [_row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__")],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_department_names",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.get_recipients_for_company_daily_report",
        lambda session: [
            {"user_id": "daily-1", "open_id": "ou_daily", "platform": "feishu", "name": "日报"},
            {"user_id": "daily-2", "open_id": "ou_daily_2", "platform": "feishu", "name": "日报二"},
        ],
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.recipients_from_user_ids",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("named list should not be used")),
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.deliver_company_highlight_card",
        lambda session, recipient, assembled: sent.append(recipient["user_id"]) or {"success": True},
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.report_today_highlights_usage",
        lambda *args, **kwargs: None,
    )
    result = handle_company_today_highlights(MagicMock(), report_date=MONDAY)
    assert result["sent_user_ids"] == ["daily-1", "daily-2"]
    assert sent == ["daily-1", "daily-2"]

    narrowed = handle_company_today_highlights(
        MagicMock(),
        report_date=MONDAY,
        recipient_user_ids=["daily-2", "outsider"],
    )
    assert narrowed["sent_user_ids"] == ["daily-2"]

    hidden = highlights_for_viewer(MagicMock(), "outsider", MONDAY)
    assert hidden["items"] == {}
    visible = highlights_for_viewer(MagicMock(), "daily-1", MONDAY)
    assert visible["card_links"][0]["title"] == "公司人效"


def test_executive_roles_can_open_the_page_without_being_recipients(monkeypatch):
    executive_id = "11111111-1111-1111-1111-111111111111"
    outsider_id = "22222222-2222-2222-2222-222222222222"
    _enable(monkeypatch, "user-1")
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.viewer_is_company_manager",
        lambda user_id: str(user_id) == executive_id,
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        lambda *args, **kwargs: [_row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__")],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_department_names",
        lambda *args, **kwargs: {},
    )
    visible = highlights_for_viewer(MagicMock(), executive_id, MONDAY)
    assert visible["card_links"][0]["title"] == "公司人效"
    hidden = highlights_for_viewer(MagicMock(), outsider_id, MONDAY)
    assert hidden["items"] == {}
    assert hidden["card_links"] == []


def test_page_is_empty_for_users_outside_the_list(monkeypatch):
    _enable(monkeypatch, "user-1")
    called = {"rows": 0}

    def fake_rows(*args, **kwargs):
        called["rows"] += 1
        return [_row("CXO_EFFICIENCY_INSIGHT", "__COMPANY__", summary="摘要", detail="全文")]

    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        fake_rows,
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_department_names",
        lambda *args, **kwargs: {},
    )
    hidden = highlights_for_viewer(MagicMock(), "user-2", MONDAY)
    assert hidden["items"] == {}
    assert hidden["card_links"] == []
    assert called["rows"] == 0

    visible = highlights_for_viewer(MagicMock(), "user-1", MONDAY)
    assert visible["report_date"] == MONDAY.isoformat()
    assert "period_key" not in visible
    assert visible["items"]["cxo_efficiency_insight"][0]["department_name"] == COMPANY_SCOPE_NAME
    assert visible["items"]["cxo_efficiency_insight"][0]["summary"] == "摘要"
    assert visible["card_links"][0]["title"] == "公司人效"
    assert called["rows"] == 1
