"""leader_meeting_review_card reads extracts and sends one card to each recipient."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.routes.notification import _handle_leader_meeting_review_card_push
from app.api.routes.notification_schemas import (
    LeaderMeetingReviewCardPushRequest,
    PushNotificationRequest,
)
from app.services.leader_meeting_review_card import (
    assemble_extracts,
    extracts_for_viewer,
    feishu_card_parts,
    render_dingtalk_markdown,
    summary_from_report_content,
)


def _row(
    item_type,
    claim,
    *,
    confidence,
    unique_id,
    payload_extra=None,
    subject_id="uploader-1",
    status="PENDING",
):
    payload = {"claim": claim, "confidence": confidence}
    payload.update(payload_extra or {})
    return SimpleNamespace(
        unique_id=unique_id,
        item_type=item_type,
        subject_id=subject_id,
        subject_type="USER",
        status=status,
        is_deleted=0,
        report_date=date(2026, 10, 28),
        payload=payload,
        source_report_unique_id="RPT-1",
    )


def test_push_union_accepts_leader_meeting_review_card():
    payload = TypeAdapter(PushNotificationRequest).validate_python(
        {
            "type": "leader_meeting_review_card",
            "report_id": "RPT-1",
            "owner_id": "uploader-1",
            "report_date": "2026-10-28",
        }
    )
    assert isinstance(payload, LeaderMeetingReviewCardPushRequest)


def test_leader_meeting_review_card_requires_report_and_owner():
    with pytest.raises(ValidationError):
        LeaderMeetingReviewCardPushRequest(report_id="", owner_id="uploader-1")
    with pytest.raises(ValidationError):
        LeaderMeetingReviewCardPushRequest(report_id="RPT-1", owner_id="")


def test_summary_reads_the_json_field():
    raw = '{"format": "markdown", "summary": "  团队周例会  ", "content": "ignored"}'
    assert summary_from_report_content(raw) == "团队周例会"
    assert summary_from_report_content({"summary": "一句"}) == "一句"
    assert summary_from_report_content({"content": "没有总结"}) == ""
    assert summary_from_report_content("not-json") == ""


def test_card_puts_summary_then_report_link_then_extract_buttons(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.build_leader_meeting_review_section_url",
        lambda report_id, report_date, section: f"https://example.com/{section}",
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.build_leader_meeting_review_report_url",
        lambda report_id, report_date="": f"https://example.com/v2/business/leader-meeting-review/{report_id}",
    )
    rows = [
        _row(
            "MANAGEMENT_EVENT",
            "经营事项不进正文",
            confidence=0.99,
            unique_id="event-1",
            payload_extra={"business_object": "甲客户", "management_issue": "关键人"},
        ),
        _row(
            "EMPLOYEE_TODO_CANDIDATE",
            "低置信",
            confidence=0.2,
            unique_id="todo-low",
            payload_extra={"assignee_name": "李四", "action": "补材料", "due_text": "下周一"},
        ),
        _row(
            "EMPLOYEE_TODO_CANDIDATE",
            "确认流程",
            confidence=0.8,
            unique_id="todo-1",
            payload_extra={
                "assignee_name": "王磊",
                "action": "确认预算审批流程",
                "due_text": "本周五",
                "verification_criteria": "拿到参与人",
            },
        ),
    ]
    assembled = assemble_extracts(rows, "RPT-1", date(2026, 10, 28))
    assembled["summary"] = "团队周例会：推进 POC。"
    assert list(assembled["items"]) == ["employee_todo_candidates", "management_events"]
    body, report, buttons = feishu_card_parts(assembled)
    assert body == "团队周例会：推进 POC。"
    assert report == "[完整复盘报告](https://example.com/v2/business/leader-meeting-review/RPT-1)"
    assert buttons == [
        ("待办候选（2）", "https://example.com/employee_todo_candidates"),
        ("经营事项", "https://example.com/management_events"),
    ]
    dingtalk = render_dingtalk_markdown(assembled, title="例会复盘｜2026-10-28")
    assert dingtalk.startswith("### 例会复盘｜2026-10-28\n\u00a0\n团队周例会：推进 POC。\n")
    assert "[完整复盘报告](https://example.com/v2/business/leader-meeting-review/RPT-1)" in dingtalk
    assert "[待办候选（2）](https://example.com/employee_todo_candidates)" in dingtalk
    assert "[经营事项](https://example.com/management_events)" in dingtalk
    assert "确认预算审批流程" not in dingtalk
    assert "经营事项不进正文" not in dingtalk
    event = assembled["items"]["management_events"][0]
    assert event["business_object"] == "甲客户"
    assert event["management_issue"] == "关键人"
    todo = assembled["items"]["employee_todo_candidates"][0]
    assert todo["unique_id"] == "todo-1"
    assert todo["assignee_name"] == "王磊"
    assert todo["verification_criteria"] == "拿到参与人"


def test_viewer_without_rows_gets_an_empty_payload(monkeypatch):
    seen = {}

    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_report_extract_rows",
        lambda session, report_id: [
            _row(
                "EMPLOYEE_TODO_CANDIDATE",
                "确认流程",
                confidence=0.4,
                unique_id="todo-1",
                payload_extra={"assignee_name": "王磊", "action": "确认流程"},
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.reporting_leader_tokens",
        lambda session, uploader_oauth_id: set(),
    )
    own = extracts_for_viewer(MagicMock(), "RPT-1", "uploader-1")
    assert list(own["items"]) == ["employee_todo_candidates"]
    assert own["can_manage"] is True
    other = extracts_for_viewer(MagicMock(), "RPT-1", "someone-else")
    assert list(other["items"]) == ["employee_todo_candidates"]
    assert other["can_manage"] is False
    missing = extracts_for_viewer(MagicMock(), "RPT-1", "")
    assert list(missing["items"]) == ["employee_todo_candidates"]
    assert missing["can_manage"] is False


def test_push_skips_when_empty_or_missing_open_id(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_extract_rows",
        lambda session, report_id, owner_id: [],
    )
    empty = _handle_leader_meeting_review_card_push(
        MagicMock(),
        LeaderMeetingReviewCardPushRequest(report_id="RPT-1", owner_id="uploader-1"),
    )
    assert empty["pushed"] is False
    assert empty["reason"] == "empty_extracts"

    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_extract_rows",
        lambda session, report_id, owner_id: [
            _row(
                "EMPLOYEE_TODO_CANDIDATE",
                "确认流程",
                confidence=0.5,
                unique_id="todo-1",
                payload_extra={"assignee_name": "王磊", "action": "确认流程", "due_text": "本周五"},
            )
        ],
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: None,
    )
    missing = _handle_leader_meeting_review_card_push(
        MagicMock(),
        LeaderMeetingReviewCardPushRequest(
            report_id="RPT-1",
            owner_id="uploader-1",
            report_date="2026-10-28",
        ),
    )
    assert missing["pushed"] is False
    assert missing["reason"] == "no_open_id"
    assert missing["report_date"] == "2026-10-28"


def test_push_sends_when_the_uploader_can_be_reached(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_extract_rows",
        lambda session, report_id, owner_id: [
            _row(
                "EMPLOYEE_TODO_CANDIDATE",
                "确认流程",
                confidence=0.8,
                unique_id="todo-1",
                payload_extra={
                    "assignee_name": "王磊",
                    "action": "确认流程",
                    "due_text": "本周五",
                    "verification_criteria": "拿到名单",
                },
            )
        ],
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: SimpleNamespace(
            is_active=True,
            user_id="user-1",
            name="王经理",
            department="华北",
            oauth_user=SimpleNamespace(open_id="ou_1", provider="feishu"),
        ),
    )

    def fake_deliver(session, recipient, assembled):
        sent["recipient"] = recipient
        sent["assembled"] = assembled
        return {"success": True}

    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.deliver_card",
        fake_deliver,
    )
    result = _handle_leader_meeting_review_card_push(
        MagicMock(),
        LeaderMeetingReviewCardPushRequest(report_id="RPT-1", owner_id="uploader-1"),
    )
    assert result["pushed"] is True
    assert result["reason"] is None
    assert sent["recipient"]["open_id"] == "ou_1"
    assert "employee_todo_candidates" in sent["assembled"]["items"]
    assert "management_events" not in sent["assembled"]["items"]


def test_push_sends_the_same_card_to_owner_and_creator(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_extract_rows",
        lambda session, report_id, owner_id: [
            _row(
                "EMPLOYEE_TODO_CANDIDATE",
                "确认流程",
                confidence=0.9,
                unique_id="todo-1",
                payload_extra={"assignee_name": "王磊", "action": "确认流程", "due_text": "本周五"},
            )
        ],
    )

    def profile_for(session, oauth_id):
        if oauth_id == "missing-open-id":
            return None
        return SimpleNamespace(
            is_active=True,
            user_id=oauth_id,
            name=oauth_id,
            department="华北",
            oauth_user=SimpleNamespace(open_id=f"ou_{oauth_id}", provider="feishu"),
        )

    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        profile_for,
    )

    def fake_deliver(session, recipient, assembled):
        sent.append(recipient["open_id"])
        return {"success": True}

    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.deliver_card",
        fake_deliver,
    )
    both = _handle_leader_meeting_review_card_push(
        MagicMock(),
        LeaderMeetingReviewCardPushRequest(
            report_id="RPT-1",
            owner_id="leader-1",
            creator_id="ops-1",
        ),
    )
    assert both["pushed"] is True
    assert both["reason"] is None
    assert both["creator_id"] == "ops-1"
    assert sent == ["ou_leader-1", "ou_ops-1"]

    sent.clear()
    same = _handle_leader_meeting_review_card_push(
        MagicMock(),
        LeaderMeetingReviewCardPushRequest(
            report_id="RPT-1",
            owner_id="leader-1",
            creator_id="leader-1",
        ),
    )
    assert same["pushed"] is True
    assert sent == ["ou_leader-1"]

    sent.clear()
    partial = _handle_leader_meeting_review_card_push(
        MagicMock(),
        LeaderMeetingReviewCardPushRequest(
            report_id="RPT-1",
            owner_id="leader-1",
            creator_id="missing-open-id",
        ),
    )
    assert partial["pushed"] is True
    assert partial["reason"] is None
    assert sent == ["ou_leader-1"]
