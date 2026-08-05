"""日报/周报卡片 receive gate 单测（个人过滤 + company by-permission）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.platforms.notification_types import (
    PERM_DAILY_REPORT_COMPANY_RECEIVE,
    PERM_DAILY_REPORT_PERSONAL_RECEIVE,
    PERM_DAILY_REPORT_TEAM_RECEIVE,
    PERM_WEEKLY_REPORT_COMPANY_RECEIVE,
    PERM_WEEKLY_REPORT_TEAM_RECEIVE,
)
from app.services.platform_notification_service import PlatformNotificationService

USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


def _svc() -> PlatformNotificationService:
    return PlatformNotificationService()


def test_parse_recipient_user_id_accepts_snake_and_camel():
    svc = _svc()
    assert svc._parse_recipient_user_id({"user_id": str(USER_A)}) == USER_A
    assert svc._parse_recipient_user_id({"userId": str(USER_B)}) == USER_B


def test_parse_recipient_user_id_rejects_missing_or_invalid():
    svc = _svc()
    assert svc._parse_recipient_user_id({}) is None
    assert svc._parse_recipient_user_id({"user_id": "not-a-uuid"}) is None


def test_user_has_receive_permission_true_when_function_allowed():
    svc = _svc()
    with patch(
        "app.services.platform_notification_service.oauth_client.check_function_permission",
        return_value={"function_allowed": True},
    ) as mock_check:
        assert svc._user_has_receive_permission(USER_A, PERM_DAILY_REPORT_TEAM_RECEIVE) is True
    mock_check.assert_called_once_with(
        user_id=USER_A,
        permission=PERM_DAILY_REPORT_TEAM_RECEIVE,
    )


def test_user_has_receive_permission_true_when_allowed_alias():
    svc = _svc()
    with patch(
        "app.services.platform_notification_service.oauth_client.check_function_permission",
        return_value={"allowed": True},
    ):
        assert svc._user_has_receive_permission(USER_A, PERM_WEEKLY_REPORT_TEAM_RECEIVE) is True


def test_user_has_receive_permission_false_when_denied():
    svc = _svc()
    with patch(
        "app.services.platform_notification_service.oauth_client.check_function_permission",
        return_value={"function_allowed": False, "allowed": False},
    ):
        assert svc._user_has_receive_permission(USER_A, PERM_DAILY_REPORT_PERSONAL_RECEIVE) is False


def test_filter_recipients_keeps_allowed_drops_denied_and_invalid():
    svc = _svc()
    recipients = [
        {"name": "ok", "user_id": str(USER_A), "open_id": "ou_a"},
        {"name": "denied", "user_id": str(USER_B), "open_id": "ou_b"},
        {"name": "no-id", "open_id": "ou_x"},
        {"name": "bad-id", "userId": "bad", "open_id": "ou_y"},
    ]

    def _perm(user_id, permission):
        assert permission == PERM_DAILY_REPORT_TEAM_RECEIVE
        return user_id == USER_A

    with patch.object(svc, "_user_has_receive_permission", side_effect=_perm):
        filtered = svc._filter_recipients_by_receive_permission(
            MagicMock(),
            recipients,
            PERM_DAILY_REPORT_TEAM_RECEIVE,
            report_kind="department daily report",
        )

    assert filtered == [recipients[0]]


def test_filter_recipients_empty_input():
    svc = _svc()
    assert (
        svc._filter_recipients_by_receive_permission(
            MagicMock(),
            None,
            PERM_WEEKLY_REPORT_TEAM_RECEIVE,
            report_kind="department weekly report",
        )
        == []
    )
    assert (
        svc._filter_recipients_by_receive_permission(
            MagicMock(),
            [],
            PERM_WEEKLY_REPORT_TEAM_RECEIVE,
            report_kind="department weekly report",
        )
        == []
    )


def test_send_department_daily_report_filters_with_team_receive_perm():
    svc = _svc()
    recipients = [{"name": "lead", "user_id": str(USER_A), "open_id": "ou_a"}]
    filtered = list(recipients)

    with patch.object(
        svc, "_filter_recipients_by_receive_permission", return_value=filtered
    ) as mock_filter, patch.object(
        svc, "_convert_daily_report_data_for_feishu", return_value={"report_date": "2026-01-01"}
    ), patch.object(
        svc, "_get_template_id_by_platform", return_value={"feishu": "tpl"}
    ), patch.object(
        svc,
        "_send_report_to_department_review_groups_or_recipients",
        return_value={"success": True},
    ) as mock_send:
        result = svc.send_department_daily_report_notification(
            MagicMock(),
            {"department_name": "Sales"},
            recipients=recipients,
        )

    assert result == {"success": True}
    mock_filter.assert_called_once()
    assert mock_filter.call_args.args[2] == PERM_DAILY_REPORT_TEAM_RECEIVE
    assert mock_filter.call_args.kwargs["report_kind"] == "department daily report"
    assert mock_send.call_args.kwargs["recipients"] == filtered


def test_send_weekly_report_filters_with_team_receive_perm():
    svc = _svc()
    recipients = [{"name": "lead", "user_id": str(USER_A), "open_id": "ou_a"}]

    with patch.object(
        svc, "_filter_recipients_by_receive_permission", return_value=[]
    ) as mock_filter, patch.object(
        svc, "_convert_weekly_report_data_for_feishu", return_value={}
    ), patch.object(
        svc, "_get_template_id_by_platform", return_value={"feishu": "tpl"}
    ), patch.object(
        svc,
        "_send_report_to_department_review_groups_or_recipients",
        return_value={"success": True, "recipients_count": 0},
    ):
        svc.send_weekly_report_notification(
            MagicMock(),
            {"department_name": "Sales"},
            recipients=recipients,
        )

    assert mock_filter.call_args.args[2] == PERM_WEEKLY_REPORT_TEAM_RECEIVE


def test_get_recipients_for_company_daily_report_uses_company_receive_perm():
    svc = _svc()
    with patch.object(
        svc,
        "_get_card_permission_receivers",
        return_value=[
            {
                "platform": "feishu",
                "open_id": "ou_c",
                "name": "Exec",
                "user_id": str(USER_A),
            }
        ],
    ) as mock_receivers, patch.object(svc, "_validate_platform_support", return_value=True):
        recipients = svc.get_recipients_for_company_daily_report(MagicMock())

    mock_receivers.assert_called_once_with(permission=PERM_DAILY_REPORT_COMPANY_RECEIVE)
    assert len(recipients) == 1
    assert recipients[0]["open_id"] == "ou_c"
    assert recipients[0]["type"] == "company_executive"
    assert recipients[0]["userId"] == str(USER_A)


def test_get_recipients_for_company_weekly_report_uses_company_receive_perm():
    svc = _svc()
    with patch.object(
        svc,
        "_get_card_permission_receivers",
        return_value=[
            {
                "platform": "feishu",
                "open_id": "ou_w",
                "name": "Exec",
            }
        ],
    ) as mock_receivers, patch.object(svc, "_validate_platform_support", return_value=True):
        recipients = svc.get_recipients_for_company_weekly_report(MagicMock())

    mock_receivers.assert_called_once_with(permission=PERM_WEEKLY_REPORT_COMPANY_RECEIVE)
    assert recipients[0]["open_id"] == "ou_w"


def test_sales_daily_report_skips_recorder_without_personal_receive():
    svc = _svc()
    profile = MagicMock()
    profile.user_id = USER_A
    profile.name = "Alice"
    profile.oauth_user = MagicMock(open_id="ou_alice", provider="feishu")

    with patch(
        "app.services.platform_notification_service.user_profile_repo.get_by_recorder_id",
        return_value=profile,
    ), patch.object(svc, "_user_has_receive_permission", return_value=False) as mock_perm:
        result = svc.get_recipients_for_sales_daily_report(
            MagicMock(),
            recorder_id=str(USER_A),
            recorder_name="Alice",
        )

    mock_perm.assert_called_once_with(USER_A, PERM_DAILY_REPORT_PERSONAL_RECEIVE)
    assert result == []


def test_sales_daily_report_includes_recorder_with_personal_receive():
    svc = _svc()
    profile = MagicMock()
    profile.user_id = USER_A
    profile.name = "Alice"
    profile.oauth_user = MagicMock(open_id="ou_alice", provider="feishu")

    with patch(
        "app.services.platform_notification_service.user_profile_repo.get_by_recorder_id",
        return_value=profile,
    ), patch.object(svc, "_user_has_receive_permission", return_value=True) as mock_perm:
        result = svc.get_recipients_for_sales_daily_report(
            MagicMock(),
            recorder_id=str(USER_A),
            recorder_name="Alice",
        )

    mock_perm.assert_called_once_with(USER_A, PERM_DAILY_REPORT_PERSONAL_RECEIVE)
    assert len(result) == 1
    assert result[0]["open_id"] == "ou_alice"
    assert result[0]["type"] == "recorder"
