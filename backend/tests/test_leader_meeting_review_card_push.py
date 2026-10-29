"""leader_meeting_review_card is accepted; card delivery is deferred."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.routes.notification import _handle_leader_meeting_review_card_push
from app.api.routes.notification_schemas import (
    LeaderMeetingReviewCardPushRequest,
    PushNotificationRequest,
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
    result = _handle_leader_meeting_review_card_push(payload)
    assert result["success"] is True
    assert result["pushed"] is False
    assert result["reason"] == "not_implemented"
    assert result["report_id"] == "RPT-1"
    assert result["owner_id"] == "uploader-1"
    assert result["report_date"] == "2026-10-28"


def test_leader_meeting_review_card_requires_report_and_owner():
    with pytest.raises(ValidationError):
        LeaderMeetingReviewCardPushRequest(report_id="", owner_id="uploader-1")
    with pytest.raises(ValidationError):
        LeaderMeetingReviewCardPushRequest(report_id="RPT-1", owner_id="")
