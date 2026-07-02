"""拜访卡片推送：飞书/钉钉等平台消息投递错误分类。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

# 用户侧不可达、重试无意义（账号停用、无机器人可用性、用户不存在等）
_NON_RETRYABLE_API_CODES = frozenset({
    230013,  # Bot has NO availability to this user
    230002,  # 用户无可见性 / 不在应用可用范围
    99992360,  # open_id 不存在
    99992361,
})

_NON_RETRYABLE_MARKERS = (
    "bot has no availability to this user",
    "user not found",
    "open_id not exist",
    "user is not visible",
    "user not in scope",
    "用户不可用",
    "用户不存在",
)


def extract_api_error_info(exc: Exception | str) -> Dict[str, Any]:
    """从异常或响应文本解析平台 API code / msg。"""
    if isinstance(exc, str):
        text = exc
        response = None
    else:
        response = getattr(exc, "response", None)
        try:
            text = response.text if response is not None else str(exc)
        except Exception:
            text = str(exc)

    code: Optional[int] = None
    msg = text
    try:
        if response is not None:
            data = response.json()
        else:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
        if isinstance(data, dict):
            raw_code = data.get("code")
            if raw_code is not None:
                code = int(raw_code)
            msg = str(data.get("msg") or data.get("message") or msg)
    except Exception:
        pass
    return {"code": code, "msg": msg, "text": text}


def is_non_retryable_delivery_error(exc: Exception | str) -> bool:
    """是否为「用户不可达、重试无意义」类错误。"""
    info = extract_api_error_info(exc)
    code = info.get("code")
    if code in _NON_RETRYABLE_API_CODES:
        return True
    lower = str(info.get("msg") or info.get("text") or "").lower()
    return any(marker in lower for marker in _NON_RETRYABLE_MARKERS)


def classify_delivery_error(exc: Exception | str) -> Dict[str, Any]:
    """
    分类消息投递错误。
    返回 retryable（是否值得重试）、error_code、error（摘要）。
    """
    info = extract_api_error_info(exc)
    code = info.get("code")
    msg = str(info.get("msg") or info.get("text") or "unknown error")
    retryable = not is_non_retryable_delivery_error(exc)
    if code == 230013:
        summary = f"飞书用户不可达(230013)，账号可能已停用或机器人无可用性: {msg}"
    elif not retryable and code is not None:
        summary = f"用户不可达({code}): {msg}"
    else:
        summary = msg
    return {
        "retryable": retryable,
        "error_code": code,
        "error": summary,
        "skip_reason": None if retryable else "user_unavailable",
    }


def split_failed_recipients_by_retryable(
    failed_recipients: Optional[list[Dict[str, Any]]],
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    """拆分为可重试失败与不可重试（跳过）失败。"""
    retryable: list[Dict[str, Any]] = []
    skipped: list[Dict[str, Any]] = []
    for item in failed_recipients or []:
        if item.get("retryable", True):
            retryable.append(item)
        else:
            skipped.append(item)
    return retryable, skipped
