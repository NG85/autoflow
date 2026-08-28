"""钉钉机器人文本消息：无跟进日报等纯文本应走 sampleMarkdown。"""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.platforms.dingtalk.client import DingTalkClient


def test_robot_text_msg_param_uses_markdown_for_plain_daily_report():
    msg_key, msg_param = DingTalkClient._robot_text_msg_param(
        "【公司日报】（2026-08-26）：当日无跟进记录。"
    )
    assert msg_key == "sampleMarkdown"
    assert msg_param["title"] == "【公司日报】（2026-08-26）：当日无跟进记录。"
    assert msg_param["text"] == "【公司日报】（2026-08-26）：当日无跟进记录。"


def test_robot_text_msg_param_doubles_newlines_and_truncates_title():
    body = "第一行标题\n第二行内容"
    msg_key, msg_param = DingTalkClient._robot_text_msg_param(body)
    assert msg_key == "sampleMarkdown"
    assert msg_param["title"] == "第一行标题"
    assert msg_param["text"] == "第一行标题\n\n第二行内容"

    long_title = "x" * 80
    _, msg_param = DingTalkClient._robot_text_msg_param(long_title)
    assert msg_param["title"] == "x" * 64


def test_send_text_message_posts_sample_markdown(monkeypatch):
    client = DingTalkClient(app_id="ding_test", app_secret="secret")
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"processQueryKey": "qk"}

    with patch("app.platforms.dingtalk.client.requests.post", return_value=mock_resp) as post:
        result = client._send_text_message(
            receive_id_type="open_id",
            receive_id="manager1234",
            text="【公司日报】（2026-08-26）：当日无跟进记录。",
            headers={"x-acs-dingtalk-access-token": "token"},
            robot_code="ding_test",
        )

    assert result["errcode"] == 0
    payload = post.call_args.kwargs["json"]
    assert payload["msgKey"] == "sampleMarkdown"
    assert payload["userIds"] == ["manager1234"]
    msg_param = json.loads(payload["msgParam"])
    assert "title" in msg_param
    assert "text" in msg_param
    assert "content" not in msg_param


def test_send_text_message_http_error_includes_body_and_raises():
    client = DingTalkClient(app_id="ding_test", app_secret="secret")
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 400
    mock_resp.text = '{"code":"invalidParameter.msgKey.invalid"}'
    mock_resp.raise_for_status.side_effect = requests.HTTPError(
        "400 Client Error", response=mock_resp
    )

    with patch("app.platforms.dingtalk.client.requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            client._send_text_message(
                receive_id_type="open_id",
                receive_id="manager1234",
                text="【公司日报】（2026-08-26）：当日无跟进记录。",
                headers={"x-acs-dingtalk-access-token": "token"},
                robot_code="ding_test",
            )
