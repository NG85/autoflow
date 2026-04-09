import logging
from typing import Any, Optional

import requests

from app.core.config import settings


logger = logging.getLogger(__name__)


def call_ark_llm(
    prompt: str,
    temperature: float = 0.3,
    response_format: Optional[dict[str, Any]] = None,
) -> str:
    """
    调用 Ark LLM 的统一工具函数。

    - 读取统一的配置：ARK_API_KEY / ARK_MODEL / ARK_API_URL
    - 采用 chat completions 风格的接口
    - 仅返回首个 choice 的 message.content 文本
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

    resp = requests.post(url, headers=headers, json=data, timeout=30)
    resp.raise_for_status()

    result = resp.json()
    # 兼容不同返回结构
    if "choices" in result and result["choices"]:
        return result["choices"][0]["message"]["content"]

    logger.warning("Ark LLM response missing 'choices', raw response: %s", result)
    return ""

