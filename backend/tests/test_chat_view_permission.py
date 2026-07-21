"""user_can_view_chat 单个会话可见性（与列表 data-scope 一致）测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.models import ChatVisibility
from app.permissions.chat_scope_translator import ChatScopeResult
from app.rag.chat.chat_service import user_can_view_chat

OWNER_ID = UUID("770e8400-e29b-41d4-a716-446655440002")
VIEWER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def _chat(*, user_id=OWNER_ID, visibility=ChatVisibility.PRIVATE, chat_type="default"):
    chat = MagicMock()
    chat.id = UUID("880e8400-e29b-41d4-a716-446655440003")
    chat.user_id = user_id
    chat.visibility = visibility
    chat.chat_type = chat_type
    return chat


def _viewer(*, is_superuser=False, user_id=VIEWER_ID):
    user = MagicMock()
    user.id = user_id
    user.is_superuser = is_superuser
    return user


def test_anonymous_chat_public_to_everyone():
    assert user_can_view_chat(_chat(user_id=None), None) is True


def test_public_chat_visible_without_user():
    chat = _chat(visibility=ChatVisibility.PUBLIC)
    assert user_can_view_chat(chat, None) is True


def test_private_chat_requires_user():
    assert user_can_view_chat(_chat(), None) is False


def test_owner_can_view():
    assert user_can_view_chat(_chat(), _viewer(user_id=OWNER_ID), MagicMock()) is True


def test_superuser_can_view():
    assert user_can_view_chat(_chat(), _viewer(is_superuser=True), MagicMock()) is True


def test_missing_session_denies_non_owner():
    assert user_can_view_chat(_chat(), _viewer(), None) is False


def test_allow_all_grants_view():
    session = MagicMock()
    with patch("app.rag.chat.chat_service.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.rag.chat.chat_service.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.rag.chat.chat_service.chat_permission_service.build_scope",
                return_value=ChatScopeResult(allow_all=True),
            ):
                assert user_can_view_chat(_chat(), _viewer(), session) is True


def test_owner_in_scope_grants_view():
    session = MagicMock()
    scope = ChatScopeResult(owner_user_ids=(str(OWNER_ID),))
    with patch("app.rag.chat.chat_service.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.rag.chat.chat_service.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.rag.chat.chat_service.chat_permission_service.build_scope",
                return_value=scope,
            ):
                assert user_can_view_chat(_chat(), _viewer(), session) is True


def test_owner_not_in_scope_denied():
    session = MagicMock()
    scope = ChatScopeResult(owner_user_ids=("990e8400-e29b-41d4-a716-446655440009",))
    with patch("app.rag.chat.chat_service.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.rag.chat.chat_service.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.rag.chat.chat_service.chat_permission_service.build_scope",
                return_value=scope,
            ):
                assert user_can_view_chat(_chat(), _viewer(), session) is False


def test_flag_disabled_denies_non_owner():
    session = MagicMock()
    with patch("app.rag.chat.chat_service.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = False
        with patch(
            "app.rag.chat.chat_service.chat_permission_service.build_scope"
        ) as build_scope:
            assert user_can_view_chat(_chat(), _viewer(), session) is False
    build_scope.assert_not_called()


def test_unmanaged_chat_type_denies_non_owner():
    session = MagicMock()
    with patch("app.rag.chat.chat_service.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.rag.chat.chat_service.chat_permission_service.resolve_entity",
            return_value=None,
        ):
            with patch(
                "app.rag.chat.chat_service.chat_permission_service.build_scope"
            ) as build_scope:
                assert user_can_view_chat(
                    _chat(chat_type="review_session"), _viewer(), session
                ) is False
    build_scope.assert_not_called()


def test_build_scope_exception_denies_non_owner():
    session = MagicMock()
    with patch("app.rag.chat.chat_service.settings") as mock_settings:
        mock_settings.CHAT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.rag.chat.chat_service.chat_permission_service.resolve_entity",
            return_value="enablement_sia_history",
        ):
            with patch(
                "app.rag.chat.chat_service.chat_permission_service.build_scope",
                side_effect=RuntimeError("oauth down"),
            ):
                assert user_can_view_chat(_chat(), _viewer(), session) is False
