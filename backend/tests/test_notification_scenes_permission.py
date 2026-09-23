"""推送场景目录 / 预览：OAuth 功能门控。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.routes.notification import (
    _require_notification_scene_permission,
    _user_has_function_permission,
)
from app.platforms.notification_types import (
    PERM_NOTIFICATION_SCENES_PREVIEW,
    PERM_NOTIFICATION_SCENES_VIEW,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_scene_permission_codes():
    assert PERM_NOTIFICATION_SCENES_VIEW == "notification:scenes:view"
    assert PERM_NOTIFICATION_SCENES_PREVIEW == "notification:scenes:preview"


def test_user_has_function_permission_true():
    with patch(
        "app.services.oauth_service.oauth_client.check_function_permission",
        return_value={"allowed": True},
    ) as mock_check:
        assert _user_has_function_permission(USER_ID, PERM_NOTIFICATION_SCENES_VIEW) is True
    mock_check.assert_called_once_with(
        user_id=USER_ID,
        permission=PERM_NOTIFICATION_SCENES_VIEW,
    )


def test_user_has_function_permission_false():
    with patch(
        "app.services.oauth_service.oauth_client.check_function_permission",
        return_value={"allowed": False, "function_allowed": False},
    ):
        assert _user_has_function_permission(USER_ID, PERM_NOTIFICATION_SCENES_PREVIEW) is False


def test_require_scene_permission_raises_403():
    user = MagicMock()
    user.id = USER_ID
    with patch(
        "app.api.routes.notification._user_has_function_permission",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as exc:
            _require_notification_scene_permission(
                user, PERM_NOTIFICATION_SCENES_PREVIEW, "无推送场景预览权限"
            )
    assert exc.value.status_code == 403
    assert exc.value.detail == "无推送场景预览权限"


def test_require_scene_permission_passes():
    user = MagicMock()
    user.id = USER_ID
    with patch(
        "app.api.routes.notification._user_has_function_permission",
        return_value=True,
    ):
        _require_notification_scene_permission(
            user, PERM_NOTIFICATION_SCENES_VIEW, "无推送场景查看权限"
        )
