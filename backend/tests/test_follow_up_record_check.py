"""visit_record 单条 OAuth check 接入测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.repositories.visit_record import VisitRecordRepo

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def _record() -> CRMSalesVisitRecord:
    return CRMSalesVisitRecord(record_id="fu-001", recorder_id=USER_ID)


def test_can_view_visit_record_uses_oauth_when_gate_enabled():
    repo = VisitRecordRepo()
    session = MagicMock()
    record = _record()

    with patch("app.repositories.visit_record.settings") as mock_settings:
        mock_settings.FOLLOW_UP_OAUTH_GATE_ENABLED = True
        with patch(
            "app.permissions.follow_up_permission_service.follow_up_permission_service.check_view",
            return_value=True,
        ) as check_view:
            assert repo._can_view_visit_record(session, USER_ID, record) is True

    check_view.assert_called_once_with(session, USER_ID, record)


def test_can_edit_visit_record_falls_back_to_legacy_when_gate_disabled():
    repo = VisitRecordRepo()
    session = MagicMock()
    record = _record()

    with patch("app.repositories.visit_record.settings") as mock_settings:
        mock_settings.FOLLOW_UP_OAUTH_GATE_ENABLED = False
        with patch.object(
            repo,
            "_legacy_can_edit_visit_record_by_recorder_id",
            return_value=False,
        ) as legacy:
            assert repo._can_edit_visit_record(session, USER_ID, record) is False

    legacy.assert_called_once_with(session, USER_ID, USER_ID)
