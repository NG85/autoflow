"""经 Sia `POST /api/v1/webhook/wecom/push` 发智能机器人消息。

本服务不持有 WECHAT_BOT_*，也不建 WebSocket；不是应用通知。
`chatid` 可为个人 userid 或群 chatid（与 `department_group_chats` 配置一致）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_PUSH_PATH = "/api/v1/webhook/wecom/push"
_DEFAULT_TIMEOUT_SEC = 15.0


class WeComPushError(RuntimeError):
    """Sia wecom/push 调用失败。"""


class WeComPushClient:
    """调 Sia 已封装的企微 bot 推送接口。"""

    def is_configured(self) -> bool:
        # Sia：SYNAPSE_TASK_CALLBACK_TOKEN 未配置时放行；本侧只要求 BASE_URL。
        return bool((getattr(settings, "WECOM_PUSH_BASE_URL", "") or "").strip())

    def push_url(self) -> str:
        base = (getattr(settings, "WECOM_PUSH_BASE_URL", "") or "").strip().rstrip("/") + "/"
        return urljoin(base, _PUSH_PATH.lstrip("/"))

    def _auth_headers(self, token: Optional[str] = None) -> Dict[str, str]:
        # 有 token 才带 Bearer；空则与 Sia「未配置则放行」对齐，不发 Authorization。
        auth = (token if token is not None else getattr(settings, "WECOM_PUSH_TOKEN", "") or "").strip()
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        return headers

    def _post_push(self, payload: Dict[str, Any], *, token: Optional[str] = None) -> Dict[str, Any]:
        if not self.is_configured():
            raise WeComPushError("wecom push is not configured")
        chatid = str(payload.get("chatid") or "").strip()
        url = self.push_url()
        try:
            resp = requests.post(
                url,
                headers=self._auth_headers(token),
                json=payload,
                timeout=_DEFAULT_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            raise WeComPushError(f"wecom push request failed: {exc}") from exc

        if resp.status_code >= 400:
            body = (resp.text or "")[:500]
            raise WeComPushError(
                f"wecom push HTTP {resp.status_code}: {body or resp.reason}"
            )

        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {"raw": (resp.text or "")[:500]}
        if isinstance(data, dict) and data.get("success") is False:
            raise WeComPushError(
                f"wecom push rejected: {data.get('message') or data.get('error') or data}"
            )
        logger.info(
            "wecom push ok chatid=%s msgtype=%s",
            chatid,
            payload.get("msgtype"),
        )
        return data if isinstance(data, dict) else {"result": data}

    def send_markdown(self, chatid: str, content: str, *, token: Optional[str] = None) -> Dict[str, Any]:
        """主动推 markdown。chatid = 企微 userid 或群 id。"""
        target = (chatid or "").strip()
        text = (content or "").strip()
        if not target:
            raise WeComPushError("wecom chatid is empty")
        if not text:
            raise WeComPushError("wecom markdown content is empty")
        return self._post_push(
            {
                "chatid": target,
                "msgtype": "markdown",
                "markdown": {"content": text},
            },
            token=token,
        )

    def send_template_card(
        self,
        chatid: str,
        template_card: Dict[str, Any],
        *,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """主动推 template_card。chatid = 企微 userid 或群 id。"""
        target = (chatid or "").strip()
        if not target:
            raise WeComPushError("wecom chatid is empty")
        if not isinstance(template_card, dict) or not template_card:
            raise WeComPushError("wecom template_card is empty")
        return self._post_push(
            {
                "chatid": target,
                "msgtype": "template_card",
                "template_card": template_card,
            },
            token=token,
        )

    def send_message(
        self,
        open_id: str,
        token: str,
        content: Any,
        receive_id_type: str = "open_id",
        *,
        msg_type: str = "text",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """对接 platform_notification_service._dispatch 签名。"""
        del receive_id_type, kwargs  # 企微 bot 只用 chatid（个人或群）
        if msg_type == "template_card":
            if not isinstance(content, dict):
                raise WeComPushError("template_card content must be a dict")
            return self.send_template_card(open_id, content, token=token)
        if msg_type not in ("text", "markdown"):
            raise WeComPushError(
                f"wecom bot push does not support msg_type={msg_type}"
            )
        text = _coerce_markdown_content(content)
        return self.send_markdown(open_id, text, token=token)


def _coerce_markdown_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        # 少数路径可能包一层 {"content": "..."} / markdown
        if isinstance(content.get("content"), str):
            return content["content"]
        markdown = content.get("markdown")
        if isinstance(markdown, dict) and isinstance(markdown.get("content"), str):
            return markdown["content"]
        if content.get("card_type"):
            raise WeComPushError(
                "pass msg_type=template_card for wecom template_card payloads"
            )
        if content.get("type") == "template" or content.get("schema"):
            raise WeComPushError(
                "wecom bot push does not support feishu interactive/template cards"
            )
    raise WeComPushError(
        f"unsupported wecom content type: {type(content).__name__}"
    )


wecom_push_client = WeComPushClient()
