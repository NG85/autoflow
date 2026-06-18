"""拜访记录修改权限与修订推卡逻辑测试。"""

# 预加载路由模块，避免 policies <-> repositories 循环导入
import app.api.routes.crm.visit_records.router as _visit_records_router  # noqa: F401

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.policies.visit_record_access import (
    VISIT_RECORD_EDIT_PERMISSION,
    VisitRecordAccessPolicy,
)
from app.services.aldebaran_service import AldebaranClient
from app.utils.date_utils import (
    get_visit_record_revise_entry_denial_reason,
    is_before_daily_cutoff,
    is_visit_record_revise_entry_allowed,
    utc_datetime_to_beijing_date,
)

_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _allow_edit_check():
    return {
        "allowed": True,
        "function_allowed": True,
        "data_allowed": True,
        "effect": "ALLOW",
        "requires_audit": True,
    }


def _deny_edit_check():
    return {
        "allowed": False,
        "function_allowed": False,
        "data_allowed": False,
        "effect": "DENY",
        "requires_audit": False,
    }


def _policy(
    user_id,
    permissions,
    *,
    subordinate_ids=None,
    is_admin=False,
):
    policy = VisitRecordAccessPolicy(
        session=MagicMock(),
        current_user_id=user_id,
        roles_and_permissions_provider=lambda _uid: {"permissions": permissions},
        is_admin_user_fn=lambda *_a, **_k: is_admin,
    )
    if subordinate_ids is not None:
        policy._my_subordinate_user_ids = subordinate_ids
    return policy


@patch("app.policies.visit_record_access.oauth_client.check_function_permission")
def test_can_edit_self_with_permission(mock_check):
    mock_check.return_value = _allow_edit_check()
    user_id = uuid4()
    policy = _policy(user_id, [])
    assert policy.can_edit_visit_record(user_id) is True
    mock_check.assert_called_once_with(
        user_id=user_id,
        permission=VISIT_RECORD_EDIT_PERMISSION,
    )


@patch("app.policies.visit_record_access.oauth_client.check_function_permission")
def test_can_edit_self_without_permission(mock_check):
    mock_check.return_value = _deny_edit_check()
    user_id = uuid4()
    policy = _policy(user_id, [])
    assert policy.can_edit_visit_record(user_id) is False


@patch("app.policies.visit_record_access.oauth_client.check_function_permission")
def test_can_edit_subordinate_with_permission(mock_check):
    mock_check.return_value = _allow_edit_check()
    supervisor_id = uuid4()
    subordinate_id = uuid4()
    policy = _policy(
        supervisor_id,
        [],
        subordinate_ids=[subordinate_id],
    )
    assert policy.can_edit_visit_record(subordinate_id) is True


@patch("app.policies.visit_record_access.oauth_client.check_function_permission")
def test_can_edit_other_without_view_access_even_with_permission(mock_check):
    mock_check.return_value = _allow_edit_check()
    editor_id = uuid4()
    other_id = uuid4()
    policy = _policy(editor_id, [], subordinate_ids=[])
    assert policy.can_edit_visit_record(other_id) is False


@patch("app.policies.visit_record_access.oauth_client.check_function_permission")
def test_can_edit_admin_without_permission_still_denied(mock_check):
    mock_check.return_value = _deny_edit_check()
    admin_id = uuid4()
    other_id = uuid4()
    policy = _policy(admin_id, [], is_admin=True)
    assert policy.can_access_single_recorder(other_id) is True
    assert policy.can_edit_visit_record(other_id) is False


def test_entry_window_same_day_only_default():
    today = date(2026, 6, 16)
    assert is_visit_record_revise_entry_allowed(
        today, today=today, entry_window_days=0, daily_cutoff_time=""
    ) is True
    assert is_visit_record_revise_entry_allowed(
        date(2026, 6, 15), today=today, entry_window_days=0, daily_cutoff_time=""
    ) is False


def test_entry_window_multi_day():
    today = date(2026, 6, 16)
    assert is_visit_record_revise_entry_allowed(
        date(2026, 6, 15), today=today, entry_window_days=2, daily_cutoff_time=""
    ) is True
    assert is_visit_record_revise_entry_allowed(
        date(2026, 6, 14), today=today, entry_window_days=2, daily_cutoff_time=""
    ) is False


def test_entry_window_unlimited():
    today = date(2026, 6, 16)
    assert is_visit_record_revise_entry_allowed(
        date(2020, 1, 1), today=today, entry_window_days=-1, daily_cutoff_time=""
    ) is True


def test_entry_window_rejects_future_entry_date():
    today = date(2026, 6, 16)
    assert is_visit_record_revise_entry_allowed(
        date(2026, 6, 17), today=today, entry_window_days=-1, daily_cutoff_time=""
    ) is False


def test_daily_cutoff_blocks_after_time():
    today = date(2026, 6, 16)
    before_cutoff = datetime(2026, 6, 16, 19, 59, 59, tzinfo=_BEIJING_TZ)
    at_cutoff = datetime(2026, 6, 16, 20, 0, 0, tzinfo=_BEIJING_TZ)
    assert is_visit_record_revise_entry_allowed(
        today,
        today=today,
        now=before_cutoff,
        entry_window_days=0,
        daily_cutoff_time="20:00",
    ) is True
    assert is_visit_record_revise_entry_allowed(
        today,
        today=today,
        now=at_cutoff,
        entry_window_days=0,
        daily_cutoff_time="20:00",
    ) is False
    assert get_visit_record_revise_entry_denial_reason(
        today,
        today=today,
        now=at_cutoff,
        entry_window_days=0,
        daily_cutoff_time="20:00",
    ) == "每日 20:00 之后不可修改拜访记录"


def test_daily_cutoff_applies_even_when_entry_window_unlimited():
    today = date(2026, 6, 16)
    after_cutoff = datetime(2026, 6, 16, 21, 0, 0, tzinfo=_BEIJING_TZ)
    assert is_visit_record_revise_entry_allowed(
        date(2020, 1, 1),
        today=today,
        now=after_cutoff,
        entry_window_days=-1,
        daily_cutoff_time="20:00",
    ) is False


def test_daily_cutoff_disabled_when_empty():
    now = datetime(2026, 6, 16, 23, 59, 59, tzinfo=_BEIJING_TZ)
    assert is_before_daily_cutoff(now=now, daily_cutoff_time="") is True


def test_utc_datetime_to_beijing_date_from_naive_utc():
    dt = datetime(2026, 6, 15, 10, 0, 0)
    assert utc_datetime_to_beijing_date(dt) == date(2026, 6, 15)


@patch("app.services.aldebaran_service.settings")
def test_trigger_visit_record_post_process_revised_message_type(mock_settings):
    mock_settings.ALDEBARAN_VISIT_RECORD_MESSAGE_TYPE = "crm.visit_record.saved"
    mock_settings.ALDEBARAN_MESSAGE_SOURCE_SYSTEM = "crm"

    client = AldebaranClient(session=MagicMock())
    client.submit_incoming_message = MagicMock(return_value={"ok": True})

    client.trigger_visit_record_post_process(
        record_id="form_20260615_test",
        message_type="crm.visit_record.revised",
        dedupe_key="crm.visit_record.revised:form_20260615_test:rev:1",
        payload={
            "record_id": "form_20260615_test",
            "revision_seq": 1,
            "revised_by_user_id": str(uuid4()),
            "changes": [],
        },
        trace_id="form_20260615_test:rev:1",
    )

    kwargs = client.submit_incoming_message.call_args.kwargs
    assert kwargs["message_type"] == "crm.visit_record.revised"
    assert kwargs["dedupe_key"] == "crm.visit_record.revised:form_20260615_test:rev:1"
