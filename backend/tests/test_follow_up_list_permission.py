"""跟进列表 data-scope 注入 visit_record_repo 测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.repositories.visit_record import VisitRecordRepo

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def test_apply_visit_record_list_permission_uses_oauth_data_scope():
    repo = VisitRecordRepo()
    session = MagicMock()
    query = MagicMock()
    perm_where = MagicMock()

    with patch(
        "app.repositories.visit_record.follow_up_permission_service.list_perm_where",
        return_value=perm_where,
    ) as list_perm:
        result = repo._apply_visit_record_list_permission(
            session,
            query,
            current_user_id=USER_ID,
        )

    list_perm.assert_called_once_with(session, USER_ID)
    query.where.assert_called_once_with(perm_where)
    assert result == query.where.return_value


def test_apply_visit_record_list_permission_skips_without_user():
    repo = VisitRecordRepo()
    session = MagicMock()
    query = MagicMock()

    result = repo._apply_visit_record_list_permission(
        session,
        query,
        current_user_id=None,
    )

    query.where.assert_not_called()
    assert result is query
