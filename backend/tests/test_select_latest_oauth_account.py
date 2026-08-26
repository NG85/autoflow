"""oauth_accounts 按 update_time / create_time 选取较新的可推送账号。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.user_oauth_account import select_latest_oauth_account


def _account(**kwargs):
    data = {
        "uid": "a",
        "provider": "feishu",
        "open_id": "ou_a",
        "update_time": None,
        "create_time": None,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def test_select_latest_oauth_account_prefers_update_time():
    older = _account(
        uid="a",
        provider="feishu",
        open_id="ou_feishu",
        update_time=datetime(2024, 1, 1),
        create_time=datetime(2026, 1, 1),
    )
    newer = _account(
        uid="b",
        provider="dingtalk",
        open_id="ou_dingtalk",
        update_time=datetime(2026, 8, 1),
        create_time=datetime(2024, 1, 1),
    )

    assert select_latest_oauth_account([older, newer]) is newer


def test_select_latest_oauth_account_falls_back_to_create_time():
    older = _account(
        uid="a",
        provider="feishu",
        open_id="ou_feishu",
        create_time=datetime(2024, 1, 1),
    )
    newer = _account(
        uid="b",
        provider="dingtalk",
        open_id="ou_dingtalk",
        create_time=datetime(2026, 8, 1),
    )

    assert select_latest_oauth_account([older, newer]) is newer


def test_select_latest_oauth_account_skips_undeliverable_newer_account():
    older = _account(
        uid="a",
        provider="feishu",
        open_id="ou_feishu",
        update_time=datetime(2024, 1, 1),
    )
    newer_no_open_id = _account(
        uid="b",
        provider="dingtalk",
        open_id=None,
        update_time=datetime(2026, 8, 1),
    )
    newer_unsupported = _account(
        uid="c",
        provider="github",
        open_id="gh_1",
        update_time=datetime(2026, 8, 2),
    )

    assert select_latest_oauth_account([older, newer_no_open_id, newer_unsupported]) is older


def test_select_latest_oauth_account_ignores_non_datetime_and_empty():
    assert select_latest_oauth_account([]) is None
    only = _account(uid="z", update_time="not-a-dt")
    assert select_latest_oauth_account([only]) is only


def test_select_latest_oauth_account_normalizes_aware_datetime():
    naive = _account(
        uid="a",
        provider="feishu",
        open_id="ou_feishu",
        update_time=datetime(2026, 8, 1, 12, 0, 0),
    )
    aware = _account(
        uid="b",
        provider="dingtalk",
        open_id="ou_dingtalk",
        update_time=datetime(2026, 8, 1, 13, 0, 0, tzinfo=timezone.utc),
    )

    assert select_latest_oauth_account([naive, aware]) is aware
