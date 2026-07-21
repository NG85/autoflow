"""chats 列表 data-scope 注入 ChatRepo._apply_list_scope 测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.chat_scope_translator import ChatScopeResult
from app.repositories.chat import ChatRepo

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
SUBORDINATE_ID = "660e8400-e29b-41d4-a716-446655440001"


def _ctx(chat_type: str):
    repo = ChatRepo()
    session = MagicMock()
    query = MagicMock()
    user = MagicMock()
    user.id = USER_ID
    user.is_superuser = False
    filters = MagicMock()
    filters.chat_type = chat_type
    return repo, session, query, user, filters


def _owner_ids_from_where_call(query) -> list:
    """从 query.where(Chat.user_id.in_([...])) 调用中取出 in_ 绑定的 owner 列表。"""
    clause = query.where.call_args.args[0]
    value = clause.right.value  # SQLAlchemy 对 in_ 展开为 BindParameter.value
    return list(value)


def test_allow_all_returns_query_untouched():
    repo, session, query, user, filters = _ctx("default")
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.repositories.chat.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.repositories.chat.chat_permission_service.build_scope",
                return_value=ChatScopeResult(allow_all=True),
            ):
                result = repo._apply_list_scope(session, user, filters, query)

    assert result is query
    query.where.assert_not_called()


def test_deny_falls_back_to_own_only():
    # deny / 无授权：仍能看到本人会话（并入当前用户），不再拒绝全部
    repo, session, query, user, filters = _ctx("client_visit_guide")
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.repositories.chat.chat_permission_service.resolve_entity",
            return_value="enablement_visit_guide_history",
        ):
            with patch(
                "app.repositories.chat.chat_permission_service.build_scope",
                return_value=ChatScopeResult(deny=True),
            ):
                result = repo._apply_list_scope(session, user, filters, query)

    query.where.assert_called_once()
    assert result is query.where.return_value


def test_owner_ids_apply_in_clause():
    repo, session, query, user, filters = _ctx("default")
    scope = ChatScopeResult(owner_user_ids=(str(USER_ID), SUBORDINATE_ID))
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.repositories.chat.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.repositories.chat.chat_permission_service.build_scope",
                return_value=scope,
            ):
                result = repo._apply_list_scope(session, user, filters, query)

    query.where.assert_called_once()
    assert result is query.where.return_value
    owner_ids = _owner_ids_from_where_call(query)
    assert UUID(str(USER_ID)) in owner_ids
    assert UUID(SUBORDINATE_ID) in owner_ids


def test_current_user_always_included_when_scope_excludes_self():
    # 仅返回下属、不含本人时，也应把当前用户并入，保证能看到自己的会话
    repo, session, query, user, filters = _ctx("default")
    scope = ChatScopeResult(owner_user_ids=(SUBORDINATE_ID,))
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.repositories.chat.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.repositories.chat.chat_permission_service.build_scope",
                return_value=scope,
            ):
                repo._apply_list_scope(session, user, filters, query)

    owner_ids = _owner_ids_from_where_call(query)
    assert UUID(str(USER_ID)) in owner_ids
    assert UUID(SUBORDINATE_ID) in owner_ids


def test_owner_ids_all_invalid_falls_back_to_own_only():
    # owner_user_ids 全部非法：过滤后为空，但仍并入当前用户 → 仅本人
    repo, session, query, user, filters = _ctx("default")
    scope = ChatScopeResult(owner_user_ids=("not-a-uuid",))
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.repositories.chat.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.repositories.chat.chat_permission_service.build_scope",
                return_value=scope,
            ):
                result = repo._apply_list_scope(session, user, filters, query)

    query.where.assert_called_once()
    assert result is query.where.return_value


def test_build_scope_exception_falls_back_to_own_only():
    repo, session, query, user, filters = _ctx("default")
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.repositories.chat.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.repositories.chat.chat_permission_service.build_scope",
                side_effect=RuntimeError("oauth down"),
            ):
                result = repo._apply_list_scope(session, user, filters, query)

    # 回退“仅本人”
    query.where.assert_called_once()
    assert result is query.where.return_value


def test_flag_disabled_falls_back_to_own_only():
    repo, session, query, user, filters = _ctx("default")
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = False
        with patch(
            "app.repositories.chat.chat_permission_service.build_scope"
        ) as build_scope:
            result = repo._apply_list_scope(session, user, filters, query)

    build_scope.assert_not_called()
    query.where.assert_called_once()
    assert result is query.where.return_value


def test_unmanaged_chat_type_falls_back_to_own_only():
    repo, session, query, user, filters = _ctx("review_session")
    with patch("app.repositories.chat.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.repositories.chat.chat_permission_service.resolve_entity",
            return_value=None,
        ):
            with patch(
                "app.repositories.chat.chat_permission_service.build_scope"
            ) as build_scope:
                result = repo._apply_list_scope(session, user, filters, query)

    build_scope.assert_not_called()
    query.where.assert_called_once()
    assert result is query.where.return_value
