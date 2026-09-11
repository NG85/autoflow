"""平台通知：schema 与飞书无模板 markdown 卡片。"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.api.routes.notification_schemas import PlatformNotificationPushRequest
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU
from app.services.platform_notification_service import PlatformNotificationService


def test_platform_notification_schema_defaults_type():
    payload = PlatformNotificationPushRequest(
        recipient_user_ids=["u1"],
        content_type="markdown",
        content="**hello**",
    )
    assert payload.type == "platform_notification"
    assert payload.title is None
    assert payload.delivery == "card"


def test_build_feishu_markdown_card_uses_explicit_title():
    card = PlatformNotificationService.build_feishu_markdown_card(
        "**正文**\n第二行",
        title="系统通知",
    )
    assert card["schema"] == "2.0"
    assert card["config"]["width_mode"] == "fill"
    assert card["header"]["title"]["content"] == "系统通知"
    assert card["body"]["elements"] == [{"tag": "markdown", "content": "**正文**\n第二行"}]


def test_build_feishu_markdown_card_falls_back_to_first_line():
    card = PlatformNotificationService.build_feishu_markdown_card("首行标题\n其余内容")
    assert card["header"]["title"]["content"] == "首行标题"
    assert card["body"]["elements"][0]["content"] == "首行标题\n其余内容"


def test_build_feishu_post_markdown_uses_md_tag():
    post = PlatformNotificationService.build_feishu_post_markdown(
        "## 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |",
        title="日报",
    )
    assert post["zh_cn"]["title"] == "日报"
    assert post["zh_cn"]["content"] == [
        [{"tag": "md", "text": "## 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |"}]
    ]


def test_send_platform_notification_markdown_uses_interactive_on_feishu():
    service = PlatformNotificationService()
    user_id = str(uuid4())
    profile = MagicMock()
    profile.oauth_user.open_id = "ou_test"
    profile.oauth_user.provider = PLATFORM_FEISHU
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=profile,
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        result = service.send_platform_notification(
            db,
            recipient_user_id=user_id,
            content="**md**",
            content_type="markdown",
            title="标题",
        )

    assert result["success"] is True
    args, kwargs = send_msg.call_args
    assert args[0] == "ou_test"
    assert args[2]["schema"] == "2.0"
    assert args[2]["body"]["elements"][0]["tag"] == "markdown"
    assert kwargs["msg_type"] == "interactive"


def test_send_platform_notification_markdown_post_delivery_on_feishu():
    service = PlatformNotificationService()
    user_id = str(uuid4())
    profile = MagicMock()
    profile.oauth_user.open_id = "ou_test"
    profile.oauth_user.provider = PLATFORM_FEISHU
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=profile,
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        result = service.send_platform_notification(
            db,
            recipient_user_id=user_id,
            content="**md**",
            content_type="markdown",
            title="标题",
            delivery="post",
        )

    assert result["success"] is True
    args, kwargs = send_msg.call_args
    assert kwargs["msg_type"] == "post"
    assert args[2]["zh_cn"]["content"][0][0]["tag"] == "md"
    assert args[2]["zh_cn"]["title"] == "标题"


def test_send_platform_notification_markdown_uses_text_on_dingtalk():
    service = PlatformNotificationService()
    user_id = str(uuid4())
    profile = MagicMock()
    profile.oauth_user.open_id = "ding_uid"
    profile.oauth_user.provider = PLATFORM_DINGTALK
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=profile,
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        result = service.send_platform_notification(
            db,
            recipient_user_id=user_id,
            content="**md**",
            content_type="markdown",
        )

    assert result["success"] is True
    args, kwargs = send_msg.call_args
    assert args[2] == "**md**"
    assert kwargs["msg_type"] == "text"


def test_send_platform_notification_text_post_delivery_on_feishu():
    service = PlatformNotificationService()
    user_id = str(uuid4())
    profile = MagicMock()
    profile.oauth_user.open_id = "ou_test"
    profile.oauth_user.provider = PLATFORM_FEISHU
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=profile,
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        result = service.send_platform_notification(
            db,
            recipient_user_id=user_id,
            content="第一行\n第二行",
            content_type="text",
            title="纯文本",
            delivery="post",
        )

    assert result["success"] is True
    assert result["delivery"] == "post"
    args, kwargs = send_msg.call_args
    assert kwargs["msg_type"] == "post"
    assert args[2]["zh_cn"]["title"] == "纯文本"
    assert args[2]["zh_cn"]["content"] == [
        [{"tag": "text", "text": "第一行"}],
        [{"tag": "text", "text": "第二行"}],
    ]


def test_send_platform_notification_text_always_text_on_feishu():
    service = PlatformNotificationService()
    user_id = str(uuid4())
    profile = MagicMock()
    profile.oauth_user.open_id = "ou_test"
    profile.oauth_user.provider = PLATFORM_FEISHU
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=profile,
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        service.send_platform_notification(
            db,
            recipient_user_id=user_id,
            content="plain",
            content_type="text",
        )

    assert send_msg.call_args.kwargs["msg_type"] == "text"
    assert send_msg.call_args.args[2] == "plain"
