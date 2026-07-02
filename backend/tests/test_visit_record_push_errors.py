"""飞书等不可达用户错误分类测试。"""

import json
from unittest.mock import MagicMock

import pytest
import requests

from app.services.visit_record_card_push_status import (
    VisitRecordCardPushStatus,
    resolve_card_push_status_from_notification_result,
)
from app.services.visit_record_push_errors import (
    classify_delivery_error,
    is_non_retryable_delivery_error,
)


def _http_error(body: dict, status_code: int = 400) -> requests.HTTPError:
    response = MagicMock()
    response.text = json.dumps(body)
    response.status_code = status_code
    response.json.return_value = body
    exc = requests.HTTPError(response=response)
    exc.response = response
    return exc


def test_is_non_retryable_delivery_error_feishu_230013():
    exc = _http_error({"code": 230013, "msg": "Bot has NO availability to this user."})
    assert is_non_retryable_delivery_error(exc) is True
    classified = classify_delivery_error(exc)
    assert classified["retryable"] is False
    assert classified["error_code"] == 230013
    assert classified["skip_reason"] == "user_unavailable"


def test_is_retryable_for_token_error():
    exc = _http_error({"code": 99991663, "msg": "Invalid access token"})
    assert is_non_retryable_delivery_error(exc) is False
    assert classify_delivery_error(exc)["retryable"] is True


def test_partial_pushed_becomes_pushed_when_only_non_retryable_failures():
    status = resolve_card_push_status_from_notification_result(
        success_count=1,
        recipients_count=2,
        failed_recipients=[
            {
                "name": "停用用户",
                "retryable": False,
                "error_code": 230013,
            }
        ],
    )
    assert status == VisitRecordCardPushStatus.PUSHED
