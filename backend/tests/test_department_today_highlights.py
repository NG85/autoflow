"""销售负责人今日重点：部门过滤、资格与跳过。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.api.routes.notification_schemas import DepartmentTodayHighlightsPushRequest
from app.services.department_today_highlights import (
    REPORTS_ONLY_LEAD,
    assemble_department_highlights,
    assemble_leader_highlights,
    card_footer_buttons,
    deliver_department_highlight_card,
    handle_department_today_highlights,
    highlights_for_leader,
    render_card_markdown,
    render_dingtalk_markdown,
)
from app.services.report_push_policy import parse_report_push_policy

DAY = date(2026, 10, 12)
TUESDAY = date(2026, 10, 13)
LINKS = [{"key": "leader_intervene", "title": "今日需介入"}]


def _row(
    payload,
    *,
    subject_id="oauth-leader",
    unique_id="u1",
    subject_type="USER",
    department_id=None,
    audience="leader",
    item_type="TODAY_INTERVENTION",
    account_id=None,
    opportunity_id=None,
    card_links=None,
    status="PENDING",
    extract_key=None,
    adopted_todo_id=None,
    created_at=None,
    feedback=None,
    feedback_comment=None,
):
    return {
        "unique_id": unique_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "department_id": department_id,
        "audience": audience,
        "status": status,
        "is_deleted": 0,
        "item_type": item_type,
        "extract_key": unique_id if extract_key is None else extract_key,
        "account_id": account_id,
        "opportunity_id": opportunity_id,
        "card_links": card_links if card_links is not None else LINKS,
        "payload": payload,
        "report_date": DAY,
        "adopted_todo_id": adopted_todo_id,
        "created_at": created_at,
        "feedback": feedback,
        "feedback_comment": feedback_comment,
    }


def _names():
    return {
        "accounts": {"acc-1": "甲客户"},
        "opportunities": {"opp-1": "乙商机"},
        "people": {"oauth-zhang": "张三", "oauth-li": "李四"},
    }


def _profile(oauth_id="oauth-leader", user_id="leader-1", open_id="ou_leader", name="王经理"):
    return SimpleNamespace(
        is_active=True,
        user_id=user_id,
        oauth_user_id=oauth_id,
        name=name,
        department="华北销售",
        oauth_user=SimpleNamespace(open_id=open_id, provider="feishu"),
    )


def _enable(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"department_highlights": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_ancestor_chains_bulk",
        lambda session, ids: {},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_week_visit_list_url",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: _profile(oauth_id=oauth_id),
    )
    monkeypatch.setattr(
        "app.services.oauth_service.oauth_client.get_departments_with_leaders",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_reports",
        lambda *args, **kwargs: [],
    )


def test_schema_defaults():
    payload = DepartmentTodayHighlightsPushRequest()
    assert payload.type == "department_today_highlights"
    assert payload.report_date is None
    assert payload.recipient_user_ids is None
    assert "department_id" not in DepartmentTodayHighlightsPushRequest.model_fields


def test_recipient_user_ids_sends_only_named_leaders(monkeypatch):
    sent = []
    loaded = {}
    _enable(monkeypatch)
    _stub_department_leaders(
        monkeypatch,
        {
            "华北销售": _leader("leader-keep"),
            "华东销售": _leader("leader-other"),
        },
    )
    profiles = {
        "oauth-keep": _profile(
            oauth_id="oauth-keep",
            user_id="leader-keep",
            open_id="ou_keep",
            name="保留",
        ),
        "oauth-other": _profile(
            oauth_id="oauth-other",
            user_id="leader-other",
            open_id="ou_other",
            name="其他",
        ),
    }
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: profiles.get(oauth_id),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.leader_oauth_id",
        lambda session, user_id: {
            "leader-keep": "oauth-keep",
            "leader-other": "oauth-other",
        }.get(str(user_id), ""),
    )

    def fake_load(session, report_date, oauth_user_ids=None):
        loaded["ids"] = list(oauth_user_ids or [])
        return [
            _row(
                {"claim": "保留事项", "related_user_id": "oauth-zhang", "confidence": 0.9},
                unique_id="keep",
                subject_id="oauth-keep",
            ),
            _row(
                {"claim": "其他人事项", "related_user_id": "oauth-li", "confidence": 0.8},
                unique_id="other",
                subject_id="oauth-other",
            ),
        ]

    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )

    def fake_send(session, recipient, assembled):
        sent.append(recipient["user_id"])
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(
        MagicMock(),
        report_date=DAY,
        recipient_user_ids=["oauth-keep", ""],
    )
    assert result["success"] is True
    assert result["success_count"] == 1
    assert sent == ["leader-keep"]
    assert loaded["ids"] == ["oauth-keep", ""]

    sent.clear()
    empty = handle_department_today_highlights(
        MagicMock(),
        report_date=DAY,
        recipient_user_ids=[],
    )
    assert empty["success_count"] == 0
    assert empty["skipped"] is True
    assert sent == []


def test_user_rows_stay_off_the_leader_card(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_week_visit_list_url",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_department_highlights_url",
        lambda report_date, department_id, section: (
            f"https://example.com/v2/department-highlights"
            f"?date={report_date}&department_id={department_id}&tab={section}"
        ),
    )
    rows = [
        _row(
            {
                "claim": "特价必须今天拍板",
                "leader_action": "今天找张三确认折扣上限",
                "related_user_id": "oauth-zhang",
                "confidence": 0.9,
            },
            unique_id="a",
            account_id="acc-1",
            opportunity_id="opp-1",
        ),
        _row(
            {
                "claim": "合同卡在法务",
                "leader_action": "今天约李四对齐条款",
                "related_user_id": "oauth-li",
                "confidence": 0.7,
            },
            unique_id="b",
        ),
        _row(
            {"claim": "一线立即处理", "why_today": "今天"},
            unique_id="sales",
            subject_type="USER",
            audience="sales",
            item_type="IMMEDIATE_ACTION",
        ),
    ]
    assembled = assemble_department_highlights(rows, DAY, "dept-1", _names(), department_name="华北销售")
    items = assembled["sections"][0]["items"]
    assert [item["related_user_name"] for item in items] == ["张三", "李四"]
    named = assemble_department_highlights(
        [_row({"claim": "合同未回传", "leader_action": "今天催霍涛", "related_user_id": "霍涛"})],
        DAY,
        "dept-1",
        _names(),
    )
    named_item = named["sections"][0]["items"][0]
    assert named_item["related_user_name"] == "霍涛"
    assert "霍涛" in render_card_markdown(named)
    assert "一线立即处理" not in str(items)
    body = render_card_markdown(assembled)
    assert "张三" in body
    assert "还有李四" in body
    assert "合同卡在法务" not in body
    assert "特价必须今天拍板" in body
    assert "今天找张三确认折扣上限" in body
    assert "甲客户" in body and "乙商机" in body
    assert "一线立即处理" not in body
    assert (
        "[更多（1）](https://example.com/v2/department-highlights"
        "?date=2026-10-12&department_id=dept-1&tab=leader_intervene)"
    ) in body
    monday_task = card_footer_buttons(assembled)
    assert [label for label, _url in monday_task] == ["团队待完成"]
    assert "owner_name=" not in monday_task[0][1]


def test_missing_snapshot_title_omits_button(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_week_visit_list_url",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_department_highlights_url",
        lambda *args, **kwargs: "https://example.com/dept",
    )
    assembled = assemble_department_highlights(
        [_row({"claim": "拍板", "leader_action": "今天确认", "related_user_id": "oauth-zhang"}, card_links=[])],
        DAY,
        "dept-1",
        _names(),
    )
    buttons = card_footer_buttons(assembled)
    assert [label for label, _url in buttons] == ["团队待完成"]
    assert "owner_name=" not in buttons[0][1]
    assert "**团队今日需介入**" in render_card_markdown(assembled)


def test_leader_query_returns_only_their_own_rows(monkeypatch):
    seen = {}

    def fake_oauth(session, user_id):
        seen["user"] = user_id
        return "oauth-leader"

    def fake_load(session, report_date, oauth_user_ids=None):
        seen["ids"] = list(oauth_user_ids or [])
        if oauth_user_ids == ["oauth-leader"]:
            return [
                _row(
                    {"claim": "我的介入", "leader_action": "今天介入", "related_user_id": "oauth-zhang"},
                    subject_id="oauth-leader",
                )
            ]
        return []

    monkeypatch.setattr("app.services.department_today_highlights.leader_oauth_id", fake_oauth)
    monkeypatch.setattr("app.services.department_today_highlights.load_department_extract_rows", fake_load)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda session, rows: _names(),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_sections",
        lambda *args, **kwargs: [],
    )
    own = highlights_for_leader(MagicMock(), "leader-1", DAY)
    assert seen["user"] == "leader-1"
    assert seen["ids"] == ["oauth-leader"]
    assert own["subject_id"] == "oauth-leader"
    assert "departments" not in own
    assert "department_id" not in own
    assert "department_name" not in own
    assert own["items"]["leader_intervene"][0]["claim"] == "我的介入"


def test_leader_query_includes_reports_without_extracts(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.leader_oauth_id",
        lambda session, user_id: "oauth-leader",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_sections",
        lambda *args, **kwargs: [
            {
                "key": "leader_efficiency_insight",
                "title": "团队人效",
                "item_type": "LEADER_EFFICIENCY_INSIGHT",
                "url": "https://example.com/v2/department-highlights?date=2026-10-12&tab=leader_efficiency_insight",
                "items": [
                    {
                        "unique_id": "insight-1",
                        "summary": "人均产出",
                        "detail_text": "详情",
                    }
                ],
            }
        ],
    )
    data = highlights_for_leader(MagicMock(), "leader-1", DAY)
    assert "departments" not in data
    assert "department_id" not in data
    assert "leader_intervene" not in data["items"]
    assert data["items"]["leader_efficiency_insight"][0]["summary"] == "人均产出"
    assert "title" not in data["items"]["leader_efficiency_insight"][0]
    assert "evidence" not in data["items"]["leader_efficiency_insight"][0]
    assert data["card_links"][0]["key"] == "leader_efficiency_insight"


def test_leader_query_returns_intervene_and_reports(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.leader_oauth_id",
        lambda session, user_id: "oauth-leader",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [_row({"claim": "我的介入", "related_user_id": "oauth-zhang"})],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda session, rows: _names(),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_sections",
        lambda *args, **kwargs: [
            {
                "key": "key_deal_review",
                "title": "重点商机",
                "item_type": "KEY_DEAL_REVIEW",
                "url": "https://example.com/v2/department-highlights?date=2026-10-12&tab=key_deal_review",
                "items": [
                    {
                        "unique_id": "insight-1",
                        "summary": "摘要",
                        "detail_text": "正文",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.leader_intervention_actions.attach_item_interactions",
        lambda *args, **kwargs: None,
    )
    data = highlights_for_leader(MagicMock(), "leader-1", DAY)
    assert list(data["items"]) == ["leader_intervene", "key_deal_review"]
    assert [link["key"] for link in data["card_links"]] == [
        "leader_intervene",
        "key_deal_review",
    ]
    assert data["items"]["leader_intervene"][0]["claim"] == "我的介入"
    assert data["items"]["key_deal_review"][0] == {
        "unique_id": "insight-1",
        "summary": "摘要",
        "detail_text": "正文",
    }


def test_reextract_shows_the_latest_item_without_the_previous_decision():
    rows = [
        _row(
            {"claim": "旧建议", "leader_action": "昨天的动作"},
            unique_id="old",
            extract_key="same-key",
            status="ADOPTED",
            adopted_todo_id="todo-1",
            created_at="2026-10-12T08:00:00",
            feedback="up",
            feedback_comment="判断准确",
        ),
        _row(
            {"claim": "新建议", "leader_action": "今天的动作"},
            unique_id="new",
            extract_key="same-key",
            status="PENDING",
            created_at="2026-10-12T10:00:00",
        ),
    ]
    assembled = assemble_department_highlights(rows, DAY, "dept-1", _names(), department_name="华北销售")
    items = assembled["sections"][0]["items"]
    assert len(items) == 1
    assert items[0]["unique_id"] == "new"
    assert items[0]["claim"] == "新建议"
    assert items[0]["leader_action"] == "今天的动作"
    assert items[0]["status"] == "PENDING"
    assert items[0]["adopted_todo_id"] is None
    assert items[0]["feedback"] is None
    assert items[0]["feedback_comment"] is None


def test_single_item_card_links_to_the_page(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_department_highlights_url",
        lambda report_date, department_id, section: (
            f"https://example.com/v2/department-highlights"
            f"?date={report_date}&department_id={department_id}&tab={section}"
        ),
    )
    assembled = assemble_department_highlights(
        [_row({"claim": "只有这一条", "leader_action": "今天处理", "related_user_id": "oauth-zhang"})],
        DAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    body = render_card_markdown(assembled)
    assert "更多" not in body
    assert "[今日需介入]" not in body
    assert "**团队今日需介入**" in body
    assert (
        "[查看](https://example.com/v2/department-highlights"
        "?date=2026-10-12&department_id=dept-1&tab=leader_intervene)"
    ) in body


def test_variant_disabled_skips_without_reading_rows(monkeypatch):
    called = {"load": 0}

    def fake_load(*args, **kwargs):
        called["load"] += 1
        return []

    monkeypatch.setattr(
        "app.services.department_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"department_highlights": {"today_highlights": False}}),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        fake_load,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["skipped"] is True
    assert result["skip_reason"] == "variant_disabled"
    assert called["load"] == 0


def test_empty_department_skips_without_empty_card(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.oauth_service.oauth_client.get_departments_with_leaders",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "一线事项"},
                subject_type="USER",
                audience="sales",
                item_type="IMMEDIATE_ACTION",
            )
        ],
    )
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    for day in (DAY, TUESDAY):
        sent["count"] = 0
        result = handle_department_today_highlights(MagicMock(), report_date=day)
        assert result["skipped"] is True
        assert result["skip_reason"] == "empty_highlights"
        assert sent["count"] == 0
        assert "今天没有重点" not in str(result)


def _stub_department_leaders(monkeypatch, departments):
    monkeypatch.setattr(
        "app.services.oauth_service.oauth_client.get_departments_with_leaders",
        lambda *args, **kwargs: {name: ["leader"] for name in departments},
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_ids_by_name",
        lambda session, name: [f"dept-{name}"],
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        lambda session, name: [departments[name]],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_visit_watch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [],
    )


def test_reports_only_sends_card_on_weekday(monkeypatch):
    captured = {}
    _enable(monkeypatch)
    _stub_department_leaders(
        monkeypatch,
        {"华北销售": _leader("leader-1")},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.leader_oauth_id",
        lambda session, user_id: "oauth-leader",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_reports",
        lambda *args, **kwargs: [
            {
                "key": "key_deal_review",
                "label": "重点商机",
                "url": "https://example.com/v2/department-highlights?date=2026-10-13&tab=key_deal_review",
            }
        ],
    )

    def fake_send(session, recipient, assembled):
        captured["recipient"] = recipient
        captured["body"] = render_card_markdown(assembled)
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=TUESDAY)
    assert result["success"] is True
    assert result["success_count"] == 1
    assert captured["recipient"]["user_id"] == "leader-1"
    assert captured["body"].startswith(f"**团队今日需介入**\n{REPORTS_ONLY_LEAD}")
    assert (
        "[团队重点商机](https://example.com/v2/department-highlights"
        "?date=2026-10-13&tab=key_deal_review)"
    ) in captured["body"]


def test_reports_only_leader_still_gets_card_when_others_have_extracts(monkeypatch):
    captured = []
    _enable(monkeypatch)
    _stub_department_leaders(
        monkeypatch,
        {
            "华北销售": _leader("leader-north"),
            "华东销售": _leader("leader-east"),
        },
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "华北事项", "related_user_id": "oauth-zhang", "confidence": 0.9},
                unique_id="north",
                subject_id="oauth-north",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )

    def profile(session, oauth_id):
        return _profile(oauth_id=oauth_id, user_id="leader-north", open_id="ou_leader-north")

    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        profile,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.leader_oauth_id",
        lambda session, user_id: "oauth-east" if user_id == "leader-east" else "oauth-north",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_reports",
        lambda session, oauth_id, report_date: (
            [
                {
                    "key": "key_deal_review",
                    "label": "重点商机",
                    "url": "https://example.com/deals",
                }
            ]
            if oauth_id == "oauth-east"
            else []
        ),
    )

    def fake_send(session, recipient, assembled):
        captured.append(
            {"user_id": recipient["user_id"], "body": render_card_markdown(assembled)}
        )
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=TUESDAY)
    assert result["success_count"] == 2
    bodies = {item["user_id"]: item["body"] for item in captured}
    assert "华北事项" in bodies["leader-north"]
    assert "重点商机" not in bodies["leader-north"]
    assert REPORTS_ONLY_LEAD not in bodies["leader-north"]
    assert bodies["leader-east"].startswith(f"**团队今日需介入**\n{REPORTS_ONLY_LEAD}")
    assert "[团队重点商机](https://example.com/deals)" in bodies["leader-east"]


def test_opt_out_skips_send(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row({"claim": "拍板", "leader_action": "今天确认", "related_user_id": "oauth-zhang"})
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-1": "华北销售"},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        lambda session, name: [
            {
                "open_id": "ou_leader",
                "name": "王经理",
                "userId": str(uuid4()),
                "platform": "feishu",
                "department": name,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: [],
    )
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["skip_reason"] == "opted_out"
    assert sent["count"] == 0


def test_leader_open_id_receives_every_item(monkeypatch):
    captured = {}
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_department_highlights_url",
        lambda report_date, department_id, section: (
            f"https://example.com/v2/department-highlights?date={report_date}&tab={section}"
            if not department_id
            else f"https://example.com/{department_id}/{section}"
        ),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "拍板", "leader_action": "今天确认", "related_user_id": "oauth-zhang", "confidence": 0.8},
                unique_id="a",
            ),
            _row(
                {"claim": "协调资源", "leader_action": "今天拉齐", "related_user_id": "oauth-li", "confidence": 0.6},
                unique_id="b",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-1": "华北销售"},
    )
    leader_id = str(uuid4())
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: _profile(oauth_id=oauth_id, user_id=leader_id, open_id="ou_leader"),
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        lambda session, name: [
            {
                "open_id": "ou_leader",
                "name": "王经理",
                "userId": leader_id,
                "platform": "feishu",
                "department": name,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )

    def fake_send(session, recipient, assembled):
        captured["recipient"] = recipient
        captured["body"] = render_card_markdown(assembled)
        captured["buttons"] = card_footer_buttons(assembled)
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success"] is True
    assert result["success_count"] == 1
    assert captured["recipient"]["open_id"] == "ou_leader"
    assert captured["recipient"]["user_id"] == leader_id
    assert "张三" in captured["body"]
    assert "还有李四" in captured["body"]
    assert "华北销售" not in captured["body"]
    assert (
        "[更多（1）](https://example.com/v2/department-highlights"
        "?date=2026-10-12&tab=leader_intervene)"
    ) in captured["body"]
    assert "department_id" not in captured["body"]
    assert [label for label, _url in captured["buttons"]] == ["团队待完成"]
    assert "owner_name=" not in captured["buttons"][0][1]
    assert result["success_count"] == 1


def _leader(user_id, name="王经理"):
    return {
        "open_id": f"ou_{user_id}",
        "name": name,
        "userId": user_id,
        "platform": "feishu",
    }


def test_one_leader_gets_one_card_for_their_items(monkeypatch):
    captured = []
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_department_highlights_url",
        lambda report_date, department_id, section: (
            f"https://example.com/v2/department-highlights?date={report_date}&tab={section}"
            if not department_id
            else f"https://example.com/{department_id}/{section}"
        ),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "特价必须今天拍板", "leader_action": "今天确认", "related_user_id": "oauth-zhang", "confidence": 0.9},
                unique_id="north",
                subject_id="oauth-leader",
                account_id="acc-1",
                opportunity_id="opp-1",
            ),
            _row(
                {"claim": "合同卡在法务", "leader_action": "今天对齐", "related_user_id": "oauth-li", "confidence": 0.4},
                unique_id="east",
                subject_id="oauth-leader",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, people, **kwargs: list(people),
    )

    def fake_send(session, recipient, assembled):
        captured.append(
            {
                "user_id": recipient["user_id"],
                "body": render_card_markdown(assembled),
            }
        )
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    body = captured[0]["body"]
    assert captured[0]["user_id"] == "leader-1"
    assert "张三" in body
    assert "还有李四" in body
    assert "更多（1）" in body
    assert "department_id" not in body


def test_opt_out_skips_that_leader_and_sends_the_other(monkeypatch):
    captured = []
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "华北事项", "related_user_id": "oauth-zhang", "confidence": 0.9},
                unique_id="north",
                subject_id="oauth-north",
            ),
            _row(
                {"claim": "华东事项", "related_user_id": "oauth-li", "confidence": 0.5},
                unique_id="east",
                subject_id="oauth-east",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )

    def profile(session, oauth_id):
        user_id = "leader-north" if oauth_id == "oauth-north" else "leader-east"
        return _profile(oauth_id=oauth_id, user_id=user_id, open_id=f"ou_{user_id}")

    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        profile,
    )

    def keep(session, people, **kwargs):
        return [person for person in people if person.get("user_id") != "leader-north"]

    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        keep,
    )

    def fake_send(session, recipient, assembled):
        captured.append(
            {"user_id": recipient["user_id"], "body": render_card_markdown(assembled)}
        )
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    assert captured[0]["user_id"] == "leader-east"
    assert "华东事项" in captured[0]["body"]
    assert "华北事项" not in captured[0]["body"]


def test_push_keeps_rows_from_every_department(monkeypatch):
    captured = {}
    _enable(monkeypatch)

    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "华北事项", "related_user_id": "oauth-zhang", "confidence": 0.2},
                unique_id="north",
                subject_id="oauth-leader",
                department_id="dept-north",
            ),
            _row(
                {"claim": "华东事项", "related_user_id": "oauth-li", "confidence": 0.9},
                unique_id="east",
                subject_id="oauth-leader",
                department_id="dept-east",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, people, **kwargs: list(people),
    )

    def fake_send(session, recipient, assembled):
        captured["body"] = render_card_markdown(assembled)
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    assert "华东事项" in captured["body"]
    assert "张三" in captured["body"]


def test_leader_without_open_id_is_skipped(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row({"claim": "拍板", "related_user_id": "oauth-zhang"})
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: _profile(oauth_id=oauth_id, open_id=""),
    )
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["skip_reason"] == "no_open_id"
    assert sent["count"] == 0


def test_monday_omits_the_daily_report_and_other_days_use_yesterday(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    monkeypatch.setattr("app.utils.push_page_urls.settings.CRM_SALES_TASK_PAGE_URL", "/v2/task")
    monday = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        DAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    weekly = "[团队周报](https://example.com/v2/business/reports?tab=weekly-team&year=2026&week=41)"
    body = render_card_markdown(monday, owner_name="王经理")
    assert "daily-team" not in body
    assert "[团队日报](" not in body
    assert weekly in body
    assert body.index("团队今日需介入") < body.index(weekly) < body.index("**团队本周**")
    assert "周报" not in [label for label, _url in card_footer_buttons(monday, owner_name="王经理")]
    dingtalk = render_dingtalk_markdown(monday, title="团队今日重点｜2026-10-12", owner_name="王经理")
    assert "daily-team" not in dingtalk
    assert "[团队日报](" not in dingtalk
    assert weekly in dingtalk
    monday["report_entries"] = [
        {"key": "team_operating_trend", "label": "经营趋势", "url": "https://example.com/trend"},
    ]
    assert "[团队经营趋势](https://example.com/trend) ｜ " + weekly in render_card_markdown(monday)
    tuesday = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        TUESDAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    daily = "[团队日报](https://example.com/v2/business/reports?tab=daily-team&date=2026-10-12)"
    tuesday_body = render_card_markdown(tuesday, owner_name="王经理")
    assert daily in tuesday_body
    assert tuesday_body.index("团队今日需介入") < tuesday_body.index(daily)
    assert tuesday_body.index(daily) < tuesday_body.index("**团队本周**")
    tuesday["report_entries"] = [
        {"key": "team_operating_trend", "label": "经营趋势", "url": "https://example.com/trend"},
    ]
    linked = render_card_markdown(tuesday, owner_name="王经理")
    assert "[团队经营趋势](https://example.com/trend) ｜ " + daily in linked
    dingtalk_linked = render_dingtalk_markdown(
        tuesday,
        title="团队今日重点｜2026-10-13",
        owner_name="王经理",
    )
    assert "[团队经营趋势](https://example.com/trend) ｜ " + daily in dingtalk_linked
    assert "日报" not in [label for label, _url in card_footer_buttons(tuesday, owner_name="王经理")]
    assert "weekly-team" not in linked
    assert "weekly-team" not in dingtalk_linked
    monkeypatch.setattr("app.utils.push_page_urls.settings.REVIEW_REPORT_HOST", "")
    assert "[团队日报](" not in render_card_markdown(tuesday)
    assert "[团队周报](" not in render_card_markdown(monday)


def test_monday_weekly_report_uses_the_previous_iso_week(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    new_year = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        date(2027, 1, 4),
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    body = render_card_markdown(new_year)
    assert (
        "[团队周报](https://example.com/v2/business/reports?tab=weekly-team&year=2026&week=53)"
        in body
    )
    assert "[团队日报](" not in body
    week_one = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        date(2026, 1, 5),
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    assert (
        "[团队周报](https://example.com/v2/business/reports?tab=weekly-team&year=2026&week=1)"
        in render_card_markdown(week_one)
    )


def test_week_visit_button_filters_the_team(monkeypatch):
    from app.core.config import CRMDailyReportVisitDateField

    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    monkeypatch.setattr("app.utils.push_page_urls.settings.VISIT_DETAIL_PAGE_URL", "/v2/behavior")
    monkeypatch.setattr("app.utils.push_page_urls.settings.CRM_SALES_TASK_PAGE_URL", "/v2/task")
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.CRM_DAILY_REPORT_VISIT_DATE_FIELD",
        CRMDailyReportVisitDateField.LAST_MODIFIED_TIME,
    )
    monday = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        DAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    task = (
        "团队待完成",
        "https://example.com/v2/task"
        "?due_date__gte=2026-10-12"
        "&due_date__lte=2026-10-18"
        "&status__in=IN_PROGRESS"
        "&status__in=PENDING",
    )
    assert card_footer_buttons(monday) == [task]
    assert "owner_name=" not in card_footer_buttons(monday)[0][1]
    assembled = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        TUESDAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    buttons = card_footer_buttons(assembled)
    meeting = (
        "https://example.com/v2/business/leader-meeting-review"
        "?start_date=2026-10-12&end_date=2026-10-18"
    )
    assert buttons == [
        task,
        (
            "团队已拜访",
            "https://example.com/v2/behavior"
            "?visit_communication_date_start=2026-10-12"
            "&visit_communication_date_end=2026-10-18"
            "&department_name=%E5%8D%8E%E5%8C%97%E9%94%80%E5%94%AE",
        ),
        ("团队例会复盘", meeting),
    ]
    merged = assemble_leader_highlights([assembled], TUESDAY)
    assert card_footer_buttons(merged) == buttons
    assert card_footer_buttons(assembled, owner_name="王经理") == buttons
    friday = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        date(2026, 10, 16),
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    friday["visit_watch"] = {
        "label": "本周",
        "visit_total": 3,
        "url": "https://example.com/visits",
    }
    friday_body = render_card_markdown(friday)
    assert "[团队本周拜访](" not in friday_body
    assert "[团队例会复盘](" not in friday_body
    friday_buttons = card_footer_buttons(friday)
    assert [label for label, _url in friday_buttons] == ["团队待完成", "团队已拜访", "团队例会复盘"]
    assert friday_buttons[0] == task
    assert friday_buttons[2][1] == meeting
    monday_body = render_card_markdown(monday)
    assert "例会复盘" not in monday_body
    assert "例会复盘" not in [label for label, _url in card_footer_buttons(monday, owner_name="王经理")]


def test_card_title_omits_department_name(monkeypatch):
    captured = {}
    north = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang", "confidence": 0.9}, subject_id="dept-north")],
        DAY,
        "dept-north",
        _names(),
        department_name="华北销售",
    )
    east = assemble_department_highlights(
        [_row({"claim": "对齐", "related_user_id": "oauth-li", "confidence": 0.4}, unique_id="east", subject_id="dept-east")],
        DAY,
        "dept-east",
        _names(),
        department_name="华东销售",
    )
    assembled = assemble_leader_highlights([north, east], DAY)
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.build_feishu_markdown_card",
        lambda body, title=None, **kwargs: captured.update(title=title, body=body) or {},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service._get_platform_tokens",
        lambda platforms: {"feishu": "token"},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service._send_messages_to_platform",
        lambda *args, **kwargs: (1, []),
    )
    deliver_department_highlight_card(
        MagicMock(),
        {"platform": "feishu", "user_id": "leader-1", "open_id": "ou_leader"},
        assembled,
    )
    assert captured["title"] == "团队今日重点｜2026-10-12"
    assert "华北销售" not in captured["title"]


def test_feishu_card_keeps_team_highlights_in_one_section(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    assembled = assemble_department_highlights(
        [_row({"claim": "拍板", "leader_action": "今天确认", "related_user_id": "oauth-zhang"})],
        DAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    assembled["visit_watch"] = {
        "label": "上周",
        "visit_total": 0,
        "recap_by_severity": [{"severity": "HIGH", "count": 0}],
        "low_entry": [],
        "high_severity": [],
        "url": "https://example.com/visits",
    }
    assembled["report_entries"] = [
        {"key": "leader_efficiency_insight", "label": "团队人效", "url": "https://example.com/persona"},
    ]
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.build_feishu_markdown_card",
        lambda body, title=None, **kwargs: captured.update(title=title, body=body, **kwargs) or {},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service._get_platform_tokens",
        lambda platforms: {"feishu": "token"},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service._send_messages_to_platform",
        lambda *args, **kwargs: (1, []),
    )
    deliver_department_highlight_card(
        MagicMock(),
        {"platform": "feishu", "user_id": "leader-1", "open_id": "ou_leader"},
        assembled,
    )
    assert "**团队今日需介入**" in captured["body"]
    assert "**张三**" in captured["body"]
    assert "**团队上周拜访**" not in captured["body"]
    watch = captured["extra_sections"][0]
    assert "**团队上周拜访**" in watch
    assert "[团队人效](https://example.com/persona)" in watch
    assert watch.index("团队上周拜访") < watch.index("[团队人效](")
    assert captured["extra_sections"][1] == "**团队本周**"
    assert captured["divide_extra_sections"] is True
    assert [label for label, _url in (captured.get("footer_buttons") or [])] == ["团队待完成"]
    assert "owner_name=" not in (captured.get("footer_buttons") or [("","")])[0][1]
    assert "例会复盘" not in captured["body"]

    assembled["report_date"] = TUESDAY.isoformat()
    assembled["personal_highlights"] = {
        "has_content": True,
        "report_date": TUESDAY.isoformat(),
        "sections": [
            {
                "key": "immediate_today",
                "title": "立即处理",
                "url": "https://example.com/today",
                "items": [{"claim": "今天回访"}],
            }
        ],
    }
    assembled["report_entries"] = [
        {"key": "team_operating_trend", "label": "经营趋势", "url": "https://example.com/trend"},
    ]
    captured.clear()
    deliver_department_highlight_card(
        MagicMock(),
        {"platform": "feishu", "user_id": "leader-1", "open_id": "ou_leader", "name": "王经理"},
        assembled,
    )
    daily = "[团队日报](https://example.com/v2/business/reports?tab=daily-team&date=2026-10-12)"
    assert captured["body"].startswith("**我的立即处理**")
    assert "经营趋势" not in captured["body"]
    assert "**团队今日需介入**" in captured["extra_sections"][0]
    assert "**团队上周拜访**" not in captured["extra_sections"][0]
    watch = captured["extra_sections"][1]
    assert "**团队上周拜访**" in watch
    assert "[团队经营趋势](https://example.com/trend) ｜ " + daily in watch
    assert captured["extra_sections"][2] == "**团队本周**"


def test_dingtalk_card_puts_title_in_the_body(monkeypatch):
    captured = {}

    def _send(db_session, **kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.send_platform_notification",
        _send,
    )
    assembled = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        DAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    deliver_department_highlight_card(
        MagicMock(),
        {"platform": "dingtalk", "user_id": "leader-1", "open_id": "manager1"},
        assembled,
    )
    content = captured["content"]
    assert content.startswith("# 团队今日重点｜2026-10-12\n")
    assert "## 团队今日需介入\n" in content
    assert "[查看](" in content


def test_highlights_task_keeps_the_other_side_when_one_fails(monkeypatch):
    from app.tasks.cron_jobs import push_crm_today_highlights

    class _Session:
        def __enter__(self):
            return MagicMock()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.tasks.cron_jobs.Session", lambda engine: _Session())

    def explode(session, report_date=None, recipient_user_ids=None):
        raise RuntimeError("send broke")

    def ok(session, *, report_date=None, department_id=None):
        return {
            "success": True,
            "skipped": False,
            "skip_reason": None,
            "success_count": 1,
        }

    monkeypatch.setattr(
        "app.services.sales_today_highlights.handle_sales_today_highlights",
        explode,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.handle_department_today_highlights",
        ok,
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.handle_company_today_highlights",
        lambda session, *, report_date=None, recipient_user_ids=None: {
            "success": True,
            "skipped": False,
            "skip_reason": None,
            "success_count": 1,
        },
    )
    failed = push_crm_today_highlights(report_date_str="2026-10-12", audience="department")
    assert failed["success"] is True
    assert failed["sales_today_highlights"] is None
    assert failed["department_today_highlights"]["success_count"] == 1

    failed = push_crm_today_highlights(report_date_str="2026-10-12")
    assert failed["success"] is False
    assert failed["sales_today_highlights"]["skip_reason"] == "push_failed"
    assert failed["department_today_highlights"]["success_count"] == 1


def _sales_row():
    return {
        "unique_id": "sales-1",
        "subject_type": "USER",
        "subject_id": "oauth-leader",
        "audience": "sales",
        "status": "PENDING",
        "is_deleted": 0,
        "item_type": "IMMEDIATE_ACTION",
        "extract_key": "sales-1",
        "card_links": [
            {"key": "immediate_today", "title": "立即处理"},
            {"key": "open_risks", "title": "建议关注"},
        ],
        "payload": {"claim": "今天回访", "why_today": "答应今天", "confidence": 0.9},
        "report_date": DAY,
    }


def test_team_card_does_not_fold_personal_highlights(monkeypatch):
    captured = {}
    billed = []
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy(
            {
                "department_highlights": {"today_highlights": True},
                "sales_daily": {"today_highlights": True},
            }
        ),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row({"claim": "拍板", "related_user_id": "oauth-zhang", "confidence": 0.9})
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: [_sales_row()],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_visit_watch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.report_today_highlights_usage",
        lambda audience, **kwargs: billed.append(audience),
    )

    def fake_send(session, recipient, assembled):
        captured["body"] = render_card_markdown(assembled, owner_name=recipient.get("name") or "")
        captured["card"] = assembled
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    assert result["sent_oauth_user_ids"] == ["oauth-leader"]
    assert "**团队今日需介入**" in captured["body"]
    assert "personal_highlights" not in captured["card"]
    assert "**我的立即处理**" not in captured["body"]
    assert "今天回访" not in captured["body"]
    assert billed == ["department"]


def test_failed_team_card_is_not_marked_sent(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_visit_watch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: {"success": False, "message": "send failed"},
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 0
    assert result["sent_oauth_user_ids"] == []
