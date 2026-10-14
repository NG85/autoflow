"""有资格默认接收；opt-out 从实发名单剔除。"""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from app.services.notification_delivery_preference import (
    filter_opted_out_recipients,
    is_opted_out,
)
from app.services.notification_scene_catalog import (
    SCENE_COMPANY_WEEKLY,
    SCENE_VISIT_RECORD,
    VARIANT_KPI_CARD,
    VARIANT_VISIT_CARD,
    VARIANT_VISIT_REPORT,
)

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


def _session_with_rows(rows):
    session = MagicMock()
    session.exec.return_value.all.return_value = rows
    return session


def test_no_row_means_still_receiving():
    session = _session_with_rows([])
    assert (
        is_opted_out(
            session,
            user_id=USER_A,
            scene=SCENE_COMPANY_WEEKLY,
            variant=VARIANT_KPI_CARD,
        )
        is False
    )


def test_receive_false_opts_out():
    session = _session_with_rows(
        [
            SimpleNamespace(
                receive=False,
                variant=VARIANT_KPI_CARD,
                department_id="",
            )
        ]
    )
    assert (
        is_opted_out(
            session,
            user_id=USER_A,
            scene=SCENE_COMPANY_WEEKLY,
            variant=VARIANT_KPI_CARD,
        )
        is True
    )


def test_empty_variant_covers_all_variants():
    session = _session_with_rows(
        [SimpleNamespace(receive=False, variant="", department_id="")]
    )
    assert is_opted_out(
        session, user_id=USER_A, scene=SCENE_COMPANY_WEEKLY, variant=VARIANT_VISIT_REPORT
    )
    assert is_opted_out(
        session, user_id=USER_A, scene=SCENE_COMPANY_WEEKLY, variant=VARIANT_KPI_CARD
    )


def test_visit_record_cannot_opt_out():
    session = _session_with_rows(
        [SimpleNamespace(receive=False, variant="", department_id="")]
    )
    assert (
        is_opted_out(session, user_id=USER_A, scene=SCENE_VISIT_RECORD, variant=VARIANT_VISIT_CARD)
        is False
    )


def test_filter_removes_opted_out_keeps_others():
    session = _session_with_rows(
        [SimpleNamespace(receive=False, variant="", department_id="")]
    )
    kept = filter_opted_out_recipients(
        session,
        [
            {"user_id": USER_A, "open_id": "ou_a"},
            {"userId": USER_B, "open_id": "ou_b"},
        ],
        scene=SCENE_COMPANY_WEEKLY,
        variant=VARIANT_KPI_CARD,
    )
    # session 对两个 user 都返回同一 rows；两人都 opted_out
    assert kept == []

    session_none = _session_with_rows([])
    kept = filter_opted_out_recipients(
        session_none,
        [
            {"user_id": USER_A, "open_id": "ou_a"},
            {"userId": USER_B, "open_id": "ou_b"},
        ],
        scene=SCENE_COMPANY_WEEKLY,
        variant=VARIANT_KPI_CARD,
    )
    assert [r["open_id"] for r in kept] == ["ou_a", "ou_b"]


def test_department_empty_department_id_opts_out_all_departments():
    session = _session_with_rows(
        [SimpleNamespace(receive=False, variant="", department_id="")]
    )
    assert is_opted_out(
        session,
        user_id=USER_A,
        scene="department_weekly",
        variant=VARIANT_KPI_CARD,
        department_ids=["dept-1"],
    )


def test_invalid_user_id_is_not_opted_out():
    session = _session_with_rows(
        [SimpleNamespace(receive=False, variant="", department_id="")]
    )
    assert is_opted_out(session, user_id="not-a-uuid", scene=SCENE_COMPANY_WEEKLY) is False
    assert UUID(USER_A)
