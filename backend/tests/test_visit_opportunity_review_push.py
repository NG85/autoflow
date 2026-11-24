"""visit_opportunity_review 使用拜访卡片同一套接收人。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.notification_schemas import VisitOpportunityReviewPushRequest
from app.services.report_markdown_dispatch import _send_markdown_to_groups
from app.services.visit_opportunity_review_push import handle_visit_opportunity_review_push


def test_schema_type_and_defaults():
    payload = VisitOpportunityReviewPushRequest(
        record_id="VISIT-1",
        content="## 判断\n可以推进",
    )
    assert payload.type == "visit_opportunity_review"
    assert payload.content_type == "markdown"
    assert payload.delivery == "card"
    assert payload.title is None


def test_missing_visit_record_is_404():
    session = MagicMock()
    session.exec.return_value.first.return_value = None
    with patch(
        "app.services.visit_opportunity_review_push.settings.VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED",
        True,
    ), pytest.raises(HTTPException) as exc:
        handle_visit_opportunity_review_push(
            session,
            record_id="VISIT-404",
            content="正文",
        )
    assert exc.value.status_code == 404


def test_disabled_skips_without_lookup():
    from app.core.config import Settings

    assert Settings.model_fields["VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED"].default is False
    session = MagicMock()
    with patch(
        "app.services.visit_opportunity_review_push.settings.VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED",
        False,
    ):
        result = handle_visit_opportunity_review_push(
            session,
            record_id="VISIT-1",
            content="## 判断\n可以推进",
            opportunity_id="OPP-1",
        )
    session.exec.assert_not_called()
    assert result["success"] is True
    assert result["skipped"] is True
    assert result["reason"] == "feature_disabled"
    assert result["success_count"] == 0
    assert result["recipients_count"] == 0


@patch(
    "app.services.visit_opportunity_review_push.settings.VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED",
    True,
)
@patch("app.services.visit_opportunity_review_push._deliver_markdown")
@patch("app.services.visit_opportunity_review_push._send_markdown_to_groups", return_value=1)
@patch("app.services.visit_opportunity_review_push.filter_recipients_by_policy")
@patch("app.services.visit_opportunity_review_push.load_visit_record_push_policy")
@patch("app.services.visit_opportunity_review_push.platform_notification_service")
@patch("app.services.visit_opportunity_review_push.fill_sales_visit_record_fields")
@patch("app.services.visit_opportunity_review_push._crm_visit_record_row_to_push_dict")
def test_push_uses_visit_card_recipients_and_groups(
    mock_to_dict,
    mock_fill,
    mock_service,
    mock_policy_loader,
    mock_filter,
    mock_groups,
    mock_deliver,
):
    policy = MagicMock()
    policy.group_enabled.side_effect = lambda role: role in {"review", "brief"}
    mock_policy_loader.return_value = policy
    visit = {
        "record_id": "VISIT-1",
        "recorder_id": "recorder-1",
        "recorder": "销售",
        "recorder_department_id": "D1",
    }
    mock_to_dict.return_value = dict(visit)
    mock_fill.return_value = visit
    review_groups = [{"platform": "feishu", "chat_id": "oc_review"}]
    brief_groups = [{"platform": "feishu", "chat_id": "oc_brief"}]
    collected = {
        "feishu": [
            {"open_id": "ou_recorder", "type": "recorder", "name": "销售"},
            {"open_id": "ou_leader", "type": "leader", "name": "上级"},
        ]
    }
    mock_service._collect_visit_record_recipients_and_groups.return_value = (
        collected,
        review_groups,
        brief_groups,
    )
    mock_filter.return_value = collected

    session = MagicMock()
    session.exec.return_value.first.return_value = SimpleNamespace(record_id="VISIT-1")

    result = handle_visit_opportunity_review_push(
        session,
        record_id="VISIT-1",
        content="## 判断\n可以推进",
        title="商机评估报告-[核心库]",
        opportunity_id="OPP-1",
        report_id="R1",
    )

    mock_service._collect_visit_record_recipients_and_groups.assert_called_once()
    collect_kwargs = mock_service._collect_visit_record_recipients_and_groups.call_args
    assert collect_kwargs.args[2] == "recorder-1"
    assert collect_kwargs.args[3] is visit
    assert mock_deliver.call_count == 2
    sent_open_ids = {call.kwargs["open_id"] for call in mock_deliver.call_args_list}
    assert sent_open_ids == {"ou_recorder", "ou_leader"}
    assert mock_deliver.call_args_list[0].kwargs["title"] == "商机评估报告-[核心库]"
    group_arg = mock_groups.call_args.args[1]
    assert {item["chat_id"] for item in group_arg} == {"oc_review", "oc_brief"}
    assert result["success"] is True
    assert result["type"] == "visit_opportunity_review"
    assert result["recipients_count"] == 2
    assert result["groups_count"] == 2
    assert result["success_count"] == 3
    assert result["opportunity_id"] == "OPP-1"
    assert result["report_id"] == "R1"


def test_markdown_groups_include_wecom_with_its_own_card():
    service = MagicMock()
    service.build_feishu_markdown_card.return_value = {"card": "feishu"}
    service._send_content_to_group_chats.return_value = 1
    content = "## 判断\n可以推进"
    groups = [
        {"platform": "feishu", "chat_id": "oc_review"},
        {"platform": "dingtalk", "chat_id": "dt_1"},
        {"platform": "wecom", "chat_id": "wr_1"},
    ]
    sent = _send_markdown_to_groups(
        service,
        groups,
        content,
        title="商机评估报告",
        delivery="card",
    )
    assert sent == 3
    by_platform = {
        call.kwargs["platform"]: call.kwargs
        for call in service._send_content_to_group_chats.call_args_list
    }
    assert set(by_platform) == {"feishu", "dingtalk", "wecom"}
    assert by_platform["feishu"]["msg_type"] == "interactive"
    assert by_platform["feishu"]["content"] == {"card": "feishu"}
    assert by_platform["dingtalk"]["content"] == content
    assert by_platform["dingtalk"]["msg_type"] == "text"
    assert by_platform["wecom"]["msg_type"] == "template_card"
    assert by_platform["wecom"]["content"]["card_type"] == "text_notice"
    assert by_platform["wecom"]["group_chats"] == [{"platform": "wecom", "chat_id": "wr_1"}]
