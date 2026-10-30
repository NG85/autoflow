"""leader_meeting_review_card reads extracts and sends one card to the uploader."""

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
    render_card_sections,
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


def test_card_expands_the_highest_confidence_item(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.build_leader_meeting_review_section_url",
        lambda report_id, report_date, section: f"https://example.com/{section}",
    )
    rows = [
        _row(
            "MANAGEMENT_EVENT",
            "低置信",
            confidence=0.2,
            unique_id="event-low",
            payload_extra={
                "business_object": "乙客户",
                "management_issue": "报价",
                "change_summary": "报价还没定",
                "leader_management": "下周再看",
            },
        ),
        _row(
            "MANAGEMENT_EVENT",
            "高置信",
            confidence=0.9,
            unique_id="event-high",
            payload_extra={
                "business_object": "甲客户",
                "management_issue": "关键人",
                "change_summary": "路径要调整",
                "leader_management": "由区域负责人协助引荐",
            },
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
    text = "\n\n".join(render_card_sections(assembled))
    assert "**甲客户** · 关键人" in text
    assert "路径要调整" in text
    assert "由区域负责人协助引荐" in text
    assert "低置信" not in text
    assert "报价还没定" not in text
    assert "[更多（1）](https://example.com/management_events)" in text
    assert "**王磊**" in text
    assert "确认预算审批流程" in text
    assert "截止 本周五" in text
    assert "[查看](https://example.com/employee_todo_candidates)" in text
    assert assembled["card_links"][0]["title"] == "经营事项"
    assert assembled["card_links"][1]["title"] == "员工待办"
    event = assembled["items"]["management_events"][0]
    assert event["unique_id"] == "event-high"
    assert event["business_object"] == "甲客户"
    assert event["leader_management"] == "由区域负责人协助引荐"
    todo = assembled["items"]["employee_todo_candidates"][0]
    assert todo["assignee_name"] == "王磊"
    assert todo["verification_criteria"] == "拿到参与人"
    assert "员工待办候选" not in text


def test_viewer_without_rows_gets_an_empty_payload(monkeypatch):
    seen = {}

    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_report_extract_rows",
        lambda session, report_id: [
            _row(
                "MANAGEMENT_EVENT",
                "事项",
                confidence=0.4,
                unique_id="event-1",
                payload_extra={"business_object": "甲", "management_issue": "乙"},
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.reporting_leader_tokens",
        lambda session, uploader_oauth_id: set(),
    )
    own = extracts_for_viewer(MagicMock(), "RPT-1", "uploader-1")
    assert list(own["items"]) == ["management_events"]
    other = extracts_for_viewer(MagicMock(), "RPT-1", "someone-else")
    assert other["items"] == {}
    assert other["card_links"] == []
    missing = extracts_for_viewer(MagicMock(), "RPT-1", "")
    assert missing["items"] == {}


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
                "MANAGEMENT_EVENT",
                "事项",
                confidence=0.5,
                unique_id="event-1",
                payload_extra={
                    "business_object": "甲",
                    "management_issue": "乙",
                    "change_summary": "变化",
                    "leader_management": "动作",
                },
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
