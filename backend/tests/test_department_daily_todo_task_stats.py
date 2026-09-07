"""团队日报任务统计：停用账号过滤（user_profiles.is_active=false）。"""

from unittest.mock import MagicMock
from uuid import UUID

from app.repositories.user_profile import UserProfileRepo

ACTIVE = str(UUID("550e8400-e29b-41d4-a716-446655440000"))
INACTIVE = str(UUID("660e8400-e29b-41d4-a716-446655440001"))


def test_get_inactive_owner_ids_empty_input():
    assert UserProfileRepo().get_inactive_owner_ids(MagicMock(), []) == set()
    assert UserProfileRepo().get_inactive_owner_ids(MagicMock(), ["", "  "]) == set()


def test_get_inactive_owner_ids_returns_user_and_crm_ids():
    session = MagicMock()
    session.exec.return_value.all.return_value = [
        (UUID(INACTIVE), "crm-inactive"),
    ]

    result = UserProfileRepo().get_inactive_owner_ids(
        session, [ACTIVE, INACTIVE, "crm-inactive"]
    )

    assert result == {INACTIVE, "crm-inactive"}
    session.exec.assert_called_once()
