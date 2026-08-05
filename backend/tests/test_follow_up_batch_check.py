"""跟进列表行内按钮 batch-check 测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.permissions.follow_up_permission_service import FollowUpPermissionService
from app.repositories.visit_record import VisitRecordRepo

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def _record(record_id: str = "fu-001") -> CRMSalesVisitRecord:
    return CRMSalesVisitRecord(
        record_id=record_id,
        recorder_id=USER_ID,
        account_id="acc-001",
    )


def test_batch_row_permissions_maps_edit_and_delete():
    service = FollowUpPermissionService()
    session = MagicMock()
    records = [_record("fu-001"), _record("fu-002")]

    batch_results = [
        {"permission": "sales:follow_up:edit", "allowed": True},
        {"permission": "sales:follow_up:delete", "allowed": False},
        {"permission": "sales:follow_up:edit", "allowed": False},
        {"permission": "sales:follow_up:delete", "allowed": True},
    ]
    with patch(
        "app.permissions.follow_up_permission_service.oauth_client.batch_check_permissions",
        return_value=batch_results,
    ) as batch_check:
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            with patch(
                "app.permissions.follow_up_permission_service.FollowUpContextBuilder"
            ) as builder_cls:
                builder_cls.return_value.build.return_value = {"recorder_id": str(USER_ID)}
                result = service.batch_row_permissions(session, USER_ID, records)

    assert result == {
        "fu-001": {"can_edit": True, "can_delete": False},
        "fu-002": {"can_edit": False, "can_delete": True},
    }
    assert batch_check.call_count == 1
    assert len(batch_check.call_args.kwargs["checks"]) == 4


def test_resolve_row_permissions_delegates_to_batch_check():
    repo = VisitRecordRepo()
    session = MagicMock()
    records = [_record("fu-001")]
    expected = {"fu-001": {"can_edit": True, "can_delete": False}}

    with patch(
        "app.repositories.visit_record.follow_up_permission_service.batch_row_permissions",
        return_value=expected,
    ) as batch:
        perms = repo._resolve_row_permissions_for_page(
            session,
            current_user_id=USER_ID,
            records=records,
        )

    batch.assert_called_once_with(session, USER_ID, records)
    assert perms["fu-001"].can_edit is True
    assert perms["fu-001"].can_delete is False


def test_resolve_row_permissions_skipped_without_user_or_records():
    repo = VisitRecordRepo()
    session = MagicMock()

    assert (
        repo._resolve_row_permissions_for_page(
            session,
            current_user_id=None,
            records=[_record()],
        )
        == {}
    )
    assert (
        repo._resolve_row_permissions_for_page(
            session,
            current_user_id=USER_ID,
            records=[],
        )
        == {}
    )
