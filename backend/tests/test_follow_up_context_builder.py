"""FollowUpContextBuilder 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.permissions.follow_up_context_builder import FollowUpContextBuilder, follow_up_resource_id

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
SUBORDINATE_ID = UUID("660e8400-e29b-41d4-a716-446655440001")


def _record(**kwargs) -> CRMSalesVisitRecord:
    defaults = {
        "record_id": "fu-001",
        "recorder_id": SUBORDINATE_ID,
        "account_id": "acc-001",
        "opportunity_id": None,
        "partner_id": None,
    }
    defaults.update(kwargs)
    return CRMSalesVisitRecord(**defaults)


def test_build_context_for_subordinate_record():
    session = MagicMock()
    record = _record()
    with patch(
        "app.permissions.follow_up_context_builder._resolve_subordinate_user_ids",
        return_value=[SUBORDINATE_ID],
    ):
        with patch(
            "app.permissions.follow_up_context_builder._user_has_manager_role",
            return_value=False,
        ):
            context = FollowUpContextBuilder(session, USER_ID).build(record)

    assert context["recorder_id"] == str(SUBORDINATE_ID).replace("-", "")
    assert context["is_collaborator"] is False
    assert context["is_manager"] is True
    assert context["is_subordinate_creator"] is True
    assert context["account_id"] == "acc-001"
    assert context["opportunity_id"] is None


def test_build_context_self_record_not_subordinate_creator():
    session = MagicMock()
    record = _record(recorder_id=USER_ID)
    with patch(
        "app.permissions.follow_up_context_builder._resolve_subordinate_user_ids",
        return_value=[SUBORDINATE_ID],
    ):
        with patch(
            "app.permissions.follow_up_context_builder._user_has_manager_role",
            return_value=True,
        ):
            context = FollowUpContextBuilder(session, USER_ID).build(record)

    assert context["is_subordinate_creator"] is False
    assert context["is_manager"] is True


def test_follow_up_resource_id():
    assert follow_up_resource_id(_record(record_id=" fu-001 ")) == "fu-001"
    assert follow_up_resource_id(_record(record_id="")) is None
