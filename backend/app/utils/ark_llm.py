import logging
from typing import Any, Optional

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings


logger = logging.getLogger(__name__)


def _is_retryable_ark_request_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ),
    ):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = exc.response
        if resp is None:
            return True
        return resp.status_code in (429, 502, 503, 504)
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception(_is_retryable_ark_request_error),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _post_ark_llm_request(
    url: str,
    headers: dict[str, str],
    data: dict[str, Any],
    connect_timeout: float,
    read_timeout: float,
) -> requests.Response:
    resp = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=(connect_timeout, read_timeout),
    )
    resp.raise_for_status()
    return resp


def call_ark_llm(
    prompt: str,
    temperature: float = 0.3,
    response_format: Optional[dict[str, Any]] = None,
) -> str:
    """
    调用 Ark LLM 的统一工具函数。

    - 读取统一的配置：ARK_API_KEY / ARK_MODEL / ARK_API_URL / ARK_HTTP_*_TIMEOUT
    - 采用 chat completions 风格的接口
    - 仅返回首个 choice 的 message.content 文本
    - 对网络错误、超时及 429/502/503/504：首轮失败后最多再试 2 次
    """
    api_key: str = settings.ARK_API_KEY
    model: str = settings.ARK_MODEL
    url: str = settings.ARK_API_URL

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    data: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if response_format is not None:
        data["response_format"] = response_format

    resp = _post_ark_llm_request(
        url,
        headers,
        data,
        settings.ARK_HTTP_CONNECT_TIMEOUT,
        settings.ARK_HTTP_READ_TIMEOUT,
    )

    result = resp.json()
    # 兼容不同返回结构
    if "choices" in result and result["choices"]:
        return result["choices"][0]["message"]["content"]

    logger.warning("Ark LLM response missing 'choices', raw response: %s", result)
    return ""
