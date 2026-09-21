"""推送预览：资格 vs 实发。"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.notification_preview import preview_notification, user_is_eligible
from app.services.notification_scene_catalog import (
    SCENE_COMPANY_HIGHLIGHTS,
    SCENE_COMPANY_WEEKLY,
    SCENE_DEPARTMENT_HIGHLIGHTS,
    SCENE_VISIT_RECORD,
    VARIANT_KPI_CARD,
    VARIANT_TODAY_HIGHLIGHTS,
    VARIANT_VISIT_REPORT,
)
from app.services.report_push_policy import parse_report_push_policy


def test_unknown_scene_skipped():
    data = preview_notification(MagicMock(), scene="not_a_scene")
    assert data["skip_reason"] == "unknown_scene"
    assert data["eligible"] == []


def test_company_weekly_splits_opted_out_and_will_send():
    uid_keep = str(uuid4())
    uid_out = str(uuid4())
    recipients = [
        {
            "user_id": uid_keep,
            "name": "Keep",
            "open_id": "ou_keep",
            "platform": "feishu",
            "type": "weekly_report_recipient",
        },
        {
            "user_id": uid_out,
            "name": "Out",
            "open_id": "ou_out",
            "platform": "feishu",
            "type": "weekly_report_recipient",
        },
        {
            "user_id": str(uuid4()),
            "name": "NoOpen",
            "open_id": "",
            "platform": "feishu",
            "type": "weekly_report_recipient",
        },
    ]
    service = MagicMock()
    service.get_recipients_for_company_weekly_report.return_value = recipients

    def _opted(_session, *, user_id, **_kwargs):
        return user_id == uid_out

    with (
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
        patch(
            "app.services.notification_preview.load_report_push_policy",
            return_value=parse_report_push_policy({}),
        ),
        patch("app.services.notification_preview.is_opted_out", side_effect=_opted),
    ):
        data = preview_notification(
            MagicMock(),
            scene=SCENE_COMPANY_WEEKLY,
            variant=VARIANT_KPI_CARD,
        )

    assert {p["user_id"] for p in data["eligible"]} == {
        uid_keep,
        uid_out,
        recipients[2]["user_id"],
    }
    assert [p["user_id"] for p in data["will_send"]] == [uid_keep]
    assert [p["user_id"] for p in data["opted_out"]] == [uid_out]
    assert data["enabled"] is True


def test_disabled_variant_keeps_eligible_clears_will_send():
    uid = str(uuid4())
    service = MagicMock()
    service.get_recipients_for_company_weekly_report.return_value = [
        {
            "user_id": uid,
            "name": "Exec",
            "open_id": "ou_x",
            "platform": "feishu",
            "type": "weekly_report_recipient",
        }
    ]
    policy = parse_report_push_policy(
        {"company_weekly": {"kpi_card": True, "visit_report": False}}
    )
    with (
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
        patch(
            "app.services.notification_preview.load_report_push_policy",
            return_value=policy,
        ),
        patch("app.services.notification_preview.is_opted_out", return_value=False),
    ):
        data = preview_notification(
            MagicMock(),
            scene=SCENE_COMPANY_WEEKLY,
            variant=VARIANT_VISIT_REPORT,
        )
    assert data["enabled"] is False
    assert data["skip_reason"] == "variant_disabled"
    assert data["eligible"][0]["user_id"] == uid
    assert data["will_send"] == []


def test_visit_record_without_id_requires_record():
    data = preview_notification(MagicMock(), scene=SCENE_VISIT_RECORD)
    assert data["skip_reason"] in {"record_id_required", "variant_disabled"}


def test_user_not_eligible_for_visit_record_preference():
    assert (
        user_is_eligible(
            MagicMock(),
            user_id=str(uuid4()),
            scene=SCENE_VISIT_RECORD,
        )
        is False
    )


def test_user_not_on_company_list_is_not_eligible():
    service = MagicMock()
    service.get_recipients_for_company_weekly_report.return_value = []
    with (
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
        patch(
            "app.services.notification_preview.load_report_push_policy",
            return_value=parse_report_push_policy({}),
        ),
    ):
        assert (
            user_is_eligible(
                MagicMock(),
                user_id=str(uuid4()),
                scene=SCENE_COMPANY_WEEKLY,
                variant=VARIANT_KPI_CARD,
            )
            is False
        )


def test_company_highlights_uses_named_recipients_not_company_daily():
    uid = str(uuid4())
    service = MagicMock()
    service.get_recipients_for_company_highlights.return_value = [
        {
            "user_id": uid,
            "name": "CXO",
            "open_id": "ou_cxo",
            "platform": "feishu",
            "type": "named_recipient",
        }
    ]
    service.get_recipients_for_company_daily_report.return_value = [
        {
            "user_id": str(uuid4()),
            "name": "Daily",
            "open_id": "ou_daily",
            "platform": "feishu",
            "type": "company_executive",
        }
    ]
    policy = parse_report_push_policy(
        {
            "company_highlights": {
                "today_highlights": {
                    "enabled": True,
                    "recipient_user_ids": [uid],
                }
            }
        }
    )
    with (
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
        patch(
            "app.services.notification_preview.load_report_push_policy",
            return_value=policy,
        ),
        patch("app.services.notification_preview.is_opted_out", return_value=False),
    ):
        data = preview_notification(MagicMock(), scene=SCENE_COMPANY_HIGHLIGHTS)

    assert data["variant"] == VARIANT_TODAY_HIGHLIGHTS
    assert data["enabled"] is True
    assert [p["user_id"] for p in data["eligible"]] == [uid]
    assert "named_recipient" in (data["eligible"][0].get("reasons") or [])
    service.get_recipients_for_company_highlights.assert_called_once()
    service.get_recipients_for_company_daily_report.assert_not_called()


def test_company_highlights_without_named_recipients_has_empty_eligible():
    service = MagicMock()
    service.get_recipients_for_company_highlights.return_value = []
    policy = parse_report_push_policy({"company_highlights": True})
    with (
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
        patch(
            "app.services.notification_preview.load_report_push_policy",
            return_value=policy,
        ),
        patch("app.services.notification_preview.is_opted_out", return_value=False),
    ):
        data = preview_notification(MagicMock(), scene=SCENE_COMPANY_HIGHLIGHTS)

    assert data["enabled"] is True
    assert data["eligible"] == []
    assert data["will_send"] == []


def test_department_highlights_skips_groups_and_team_receive_filter():
    uid = str(uuid4())
    service = MagicMock()
    service.resolve_department_report_recipients.return_value = [
        {
            "user_id": uid,
            "name": "Leader",
            "open_id": "ou_l",
            "platform": "feishu",
            "type": "department_manager",
        }
    ]
    service._get_group_chats_by_department.return_value = [
        {"chat_id": "oc_group", "platform": "feishu", "name": "review"}
    ]
    policy = parse_report_push_policy({"department_highlights": True})
    with (
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
        patch(
            "app.services.notification_preview.load_report_push_policy",
            return_value=policy,
        ),
        patch("app.services.notification_preview.is_opted_out", return_value=False),
    ):
        data = preview_notification(
            MagicMock(),
            scene=SCENE_DEPARTMENT_HIGHLIGHTS,
            department_id="dept-1",
            department_name="销售部",
        )

    assert data["enabled"] is True
    assert data["groups"] == []
    assert [p["user_id"] for p in data["eligible"]] == [uid]
    service._get_group_chats_by_department.assert_not_called()
    service._filter_recipients_by_receive_permission.assert_not_called()
