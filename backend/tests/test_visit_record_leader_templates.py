"""拜访卡片：form leader 使用独立模板；link 不区分角色。"""

from unittest.mock import patch

import pytest

from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.services.platform_notification_service import PlatformNotificationService


@pytest.fixture
def svc():
    with patch(
        "app.services.platform_notification_service.SiteSetting.get_setting",
        return_value={},
    ):
        yield PlatformNotificationService()


@pytest.mark.parametrize("platform", [PLATFORM_FEISHU, PLATFORM_LARK])
def test_form_complete_leader_uses_new_feishu_template(svc, platform):
    assert (
        svc._get_visit_record_template_id("leader", platform, "form", "complete")
        == "AAqPWqWmbmOJw"
    )


@pytest.mark.parametrize("platform", [PLATFORM_FEISHU, PLATFORM_LARK])
def test_form_simple_leader_keeps_existing_feishu_template(svc, platform):
    assert (
        svc._get_visit_record_template_id("leader", platform, "form", "simple")
        == "AAqzQKvKzOW1z"
    )


def test_form_leader_uses_new_dingtalk_template(svc):
    assert (
        svc._get_visit_record_template_id("leader", PLATFORM_DINGTALK, "form")
        == "90d4f6e1-0ab4-40f9-9cc2-dd6dde0c41de.schema"
    )


@pytest.mark.parametrize(
    "recipient_type",
    ["recorder", "leader", "collaborative_participant"],
)
def test_link_uses_same_template_for_all_roles(svc, recipient_type):
    assert (
        svc._get_visit_record_template_id(recipient_type, PLATFORM_FEISHU, "link")
        == "AAqPWq7vsvhlu"
    )
    assert (
        svc._get_visit_record_template_id(recipient_type, PLATFORM_DINGTALK, "link")
        == "4de58997-de70-4fbf-90f7-f5a726613503.schema"
    )
