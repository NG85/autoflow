"""销售负责人今日重点：部门过滤、资格与跳过。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.api.routes.notification_schemas import DepartmentTodayHighlightsPushRequest
from app.services.department_today_highlights import (
    assemble_department_highlights,
    assemble_leader_highlights,
    card_footer_buttons,
    deliver_department_highlight_card,
    handle_department_today_highlights,
    highlights_for_leader,
    render_card_markdown,
)
from app.services.report_push_policy import parse_report_push_policy

DAY = date(2026, 10, 12)
TUESDAY = date(2026, 10, 13)
LINKS = [{"key": "leader_intervene", "title": "今日需介入"}]


def _row(
    payload,
    *,
    subject_id="dept-1",
    unique_id="u1",
    subject_type="DEPARTMENT",
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


def test_schema_defaults():
    payload = DepartmentTodayHighlightsPushRequest()
    assert payload.type == "department_today_highlights"
    assert payload.report_date is None
    assert payload.department_id is None


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
    assert "一线立即处理" not in str(items)
    body = render_card_markdown(assembled)
    assert "张三" in body
    assert "还有李四" in body
    assert "合同卡在法务" not in body
    assert "特价必须今天拍板" in body
    assert "今天找张三确认折扣上限" in body
    assert "甲客户" in body and "乙商机" in body
    assert "一线立即处理" not in body
    assert body.rstrip().endswith(
        "[更多（1）](https://example.com/v2/department-highlights"
        "?date=2026-10-12&department_id=dept-1&tab=leader_intervene)"
    )
    assert card_footer_buttons(assembled) == []


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
    assert card_footer_buttons(assembled) == []
    assert "今日需介入" not in render_card_markdown(assembled)


def test_leader_query_hides_departments_they_do_not_lead(monkeypatch):
    seen = {}

    def fake_led(session, user_id):
        seen["user"] = user_id
        return {"dept-mine"}

    def fake_load(session, report_date, department_ids=None):
        seen["ids"] = list(department_ids or [])
        return [
            _row({"claim": "我的部门", "leader_action": "今天介入", "related_user_id": "oauth-zhang"}, subject_id="dept-mine"),
            _row({"claim": "别人的部门", "leader_action": "不该看见"}, subject_id="dept-other", unique_id="other"),
        ]

    monkeypatch.setattr("app.services.department_today_highlights.department_ids_led_by", fake_led)
    monkeypatch.setattr("app.services.department_today_highlights.load_department_extract_rows", fake_load)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda session, rows: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-mine": "华北销售", "dept-other": "华南销售"},
    )
    data = highlights_for_leader(MagicMock(), "leader-1", DAY, department_id="dept-other")
    assert seen["user"] == "leader-1"
    assert data["departments"] == []
    assert "别人的部门" not in str(data)

    own = highlights_for_leader(MagicMock(), "leader-1", DAY)
    assert seen["ids"] == ["dept-mine"]
    assert own["departments"][0]["department_id"] == "dept-mine"
    assert own["departments"][0]["items"]["leader_intervene"][0]["claim"] == "我的部门"


def test_escalated_child_department_is_visible(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.department_ids_led_by",
        lambda session, user_id: {"dept-parent"},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.escalated_child_department_ids",
        lambda session, led: {"dept-child"} if "dept-parent" in led else set(),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda session, report_date, department_ids=None: [
            _row(
                {"claim": "下级没人管", "leader_action": "今天过问"},
                unique_id="child-1",
                subject_id="dept-child",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda session, rows: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-child": "华北一组"},
    )
    data = highlights_for_leader(MagicMock(), "leader-1", DAY)
    assert data["departments"][0]["department_id"] == "dept-child"
    assert data["departments"][0]["items"]["leader_intervene"][0]["claim"] == "下级没人管"


def test_reextract_keeps_one_item_and_carries_the_decision():
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
    assert items[0]["unique_id"] == "old"
    assert items[0]["claim"] == "新建议"
    assert items[0]["leader_action"] == "今天的动作"
    assert items[0]["status"] == "ADOPTED"
    assert items[0]["adopted_todo_id"] == "todo-1"
    assert items[0]["feedback"] == "up"
    assert items[0]["feedback_comment"] == "判断准确"


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
    assert "**今日需介入**" in body
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
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["skipped"] is True
    assert result["skip_reason"] == "empty_highlights"
    assert sent["count"] == 0
    assert "今天没有重点" not in str(result)


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
    assert captured["buttons"] == []
    assert result["success_count"] == 1


def _leader(user_id, name="王经理"):
    return {
        "open_id": f"ou_{user_id}",
        "name": name,
        "userId": user_id,
        "platform": "feishu",
    }


def test_one_leader_of_two_departments_gets_one_card(monkeypatch):
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
                subject_id="dept-north",
                account_id="acc-1",
                opportunity_id="opp-1",
            ),
            _row(
                {"claim": "合同卡在法务", "leader_action": "今天对齐", "related_user_id": "oauth-li", "confidence": 0.4},
                unique_id="east",
                subject_id="dept-east",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-north": "华北销售", "dept-east": "华东销售"},
    )

    def recipients(session, name):
        leader = _leader("leader-1")
        leader["department"] = name
        if name == "华北销售":
            other = _leader("leader-2", "李经理")
            other["department"] = name
            return [leader, other]
        return [leader]

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        recipients,
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
    assert result["success_count"] == 2
    by_user = {item["user_id"]: item["body"] for item in captured}
    shared = by_user["leader-1"]
    assert "张三 · 华北销售" in shared
    assert "还有李四" in shared
    assert "更多（1）" in shared
    assert "department_id" not in shared
    north_only = by_user["leader-2"]
    assert "张三" in north_only
    assert "华北销售" not in north_only
    assert "更多" not in north_only
    assert "[查看]" in north_only


def test_opt_out_removes_that_department_from_the_person_card(monkeypatch):
    captured = {}
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "华北事项", "related_user_id": "oauth-zhang", "confidence": 0.9},
                unique_id="north",
                subject_id="dept-north",
            ),
            _row(
                {"claim": "华东事项", "related_user_id": "oauth-li", "confidence": 0.5},
                unique_id="east",
                subject_id="dept-east",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-north": "华北销售", "dept-east": "华东销售"},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        lambda session, name: [_leader("leader-1")],
    )

    def keep(session, people, **kwargs):
        if kwargs.get("department_ids") == ["dept-north"]:
            return []
        return list(people)

    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        keep,
    )

    def fake_send(session, recipient, assembled):
        captured["body"] = render_card_markdown(assembled)
        captured["ids"] = list(assembled.get("department_ids") or [])
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    assert captured["ids"] == ["dept-east"]
    assert "华东事项" in captured["body"]
    assert "华北事项" not in captured["body"]
    assert "华东销售" not in captured["body"]


def test_explicit_department_id_stays_on_that_department(monkeypatch):
    captured = {}
    _enable(monkeypatch)
    seen = {}

    def fake_load(session, report_date, department_ids=None):
        seen["ids"] = list(department_ids or [])
        return [
            _row(
                {"claim": "华北事项", "related_user_id": "oauth-zhang", "confidence": 0.2},
                unique_id="north",
                subject_id="dept-north",
            ),
            _row(
                {"claim": "华东事项", "related_user_id": "oauth-li", "confidence": 0.9},
                unique_id="east",
                subject_id="dept-east",
            ),
        ]

    monkeypatch.setattr("app.services.department_today_highlights.load_department_extract_rows", fake_load)
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-north": "华北销售", "dept-east": "华东销售"},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        lambda session, name: [_leader("leader-1")],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, people, **kwargs: list(people),
    )

    def fake_send(session, recipient, assembled):
        captured["body"] = render_card_markdown(assembled)
        captured["ids"] = list(assembled.get("department_ids") or [])
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY, department_id="dept-north")
    assert result["success_count"] == 1
    assert seen["ids"] == ["dept-north"]
    assert captured["ids"] == ["dept-north"]
    assert "华北事项" in captured["body"]
    assert "华东事项" not in captured["body"]


def test_department_without_leader_goes_to_parent_leader(monkeypatch):
    captured = {}
    asked = []
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_ancestor_chains_bulk",
        lambda session, ids: {"dept-child": [("dept-child", "小组"), ("dept-parent", "华北销售")]},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row(
                {"claim": "特价必须今天拍板", "leader_action": "今天确认", "related_user_id": "oauth-zhang", "confidence": 0.9},
                unique_id="child",
                subject_id="dept-child",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-child": "小组"},
    )

    def recipients(session, name):
        asked.append(name)
        if name == "华北销售":
            return [_leader("leader-parent")]
        return []

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        recipients,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, people, **kwargs: list(people),
    )

    def fake_send(session, recipient, assembled):
        captured["user_id"] = recipient["user_id"]
        captured["body"] = render_card_markdown(assembled)
        return {"success": True}

    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        fake_send,
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    assert asked == ["小组", "华北销售"]
    assert captured["user_id"] == "leader-parent"
    assert "张三 · 小组" in captured["body"]


def test_parent_without_leader_is_not_climbed_further(monkeypatch):
    asked = []
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_ancestor_chains_bulk",
        lambda session, ids: {
            "dept-child": [
                ("dept-child", "小组"),
                ("dept-parent", "华北销售"),
                ("dept-root", "销售中心"),
            ]
        },
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row({"claim": "拍板", "related_user_id": "oauth-zhang"}, subject_id="dept-child")
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-child": "小组"},
    )

    def recipients(session, name):
        asked.append(name)
        return []

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        recipients,
    )
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["skip_reason"] == "no_open_id"
    assert sent["count"] == 0
    assert asked == ["小组", "华北销售"]


def test_leader_without_open_id_does_not_escalate(monkeypatch):
    asked = []
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_ancestor_chains_bulk",
        lambda session, ids: {"dept-child": [("dept-child", "小组"), ("dept-parent", "华北销售")]},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            _row({"claim": "拍板", "related_user_id": "oauth-zhang"}, subject_id="dept-child")
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: _names(),
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-child": "小组"},
    )

    def recipients(session, name):
        asked.append(name)
        if name == "小组":
            return [{"name": "王经理", "userId": "leader-1", "platform": "feishu"}]
        return [_leader("leader-parent")]

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.resolve_department_report_recipients",
        recipients,
    )
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["skip_reason"] == "no_open_id"
    assert sent["count"] == 0
    assert asked == ["小组"]


def test_week_visit_button_filters_visit_date_only(monkeypatch):
    from app.core.config import CRMDailyReportVisitDateField

    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    monkeypatch.setattr("app.utils.push_page_urls.settings.VISIT_DETAIL_PAGE_URL", "/v2/behavior")
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
    assert card_footer_buttons(monday) == []
    assembled = assemble_department_highlights(
        [_row({"claim": "拍板", "related_user_id": "oauth-zhang"})],
        TUESDAY,
        "dept-1",
        _names(),
        department_name="华北销售",
    )
    buttons = card_footer_buttons(assembled)
    assert buttons == [
        (
            "本周已拜访",
            "https://example.com/v2/behavior"
            "?visit_communication_date_start=2026-10-12"
            "&visit_communication_date_end=2026-10-18",
        )
    ]


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
    assert captured["title"] == "今日重点｜2026-10-12"
    assert "华北销售" not in captured["title"]


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
    assert content.startswith("# 今日重点｜2026-10-12\n")
    assert "## 今日需介入\n" in content
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
        return {"success": True, "skipped": False, "skip_reason": None, "success_count": 1}

    monkeypatch.setattr(
        "app.services.sales_today_highlights.handle_sales_today_highlights",
        explode,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.handle_department_today_highlights",
        ok,
    )
    failed = push_crm_today_highlights(report_date_str="2026-10-12", audience="department")
    assert failed["success"] is True
    assert failed["sales_today_highlights"] is None
    assert failed["department_today_highlights"]["success_count"] == 1

    failed = push_crm_today_highlights(report_date_str="2026-10-12")
    assert failed["success"] is False
    assert failed["sales_today_highlights"]["skip_reason"] == "push_failed"
    assert failed["department_today_highlights"]["success_count"] == 1
