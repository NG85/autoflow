"""将推送文案中的自家 H5 链接转为飞书 / Lark / 钉钉客户端打开协议。

短页（跟进详情、任务、评论、录入等）默认侧栏；周报 / 洞察等宽页用窗口。
外链与已经是 applink 的地址保持原样。
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from app.core.config import settings
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK

OpenMode = Literal["sidebar", "window"]

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_APPLINK_PREFIXES = (
    "https://applink.feishu.cn/",
    "https://applink.larksuite.com/",
    "https://applink.dingtalk.com/",
    "dingtalk://",
)
_DEFAULT_WINDOW_PATH_PREFIXES = (
    "/v2/business/weekly-insight",
    "/v2/business/weekly-review",
    "/v2/business/behavior-analysis",
)


def _first_party_netloc() -> str:
    host = (settings.REVIEW_REPORT_HOST or "").strip()
    if not host:
        return ""
    parsed = urlsplit(host if "://" in host else f"https://{host}")
    return (parsed.netloc or "").lower()


def _normalize_path_prefix(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        value = urlsplit(value).path or ""
    value = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value.rstrip("/") or "/"


def _window_path_prefixes() -> tuple[str, ...]:
    prefixes = list(_DEFAULT_WINDOW_PATH_PREFIXES)
    session_path = _normalize_path_prefix(getattr(settings, "REVIEW_SESSION_PAGE_URL", "") or "")
    if session_path and session_path not in prefixes:
        prefixes.append(session_path)
    return tuple(prefixes)


def is_first_party_url(url: str) -> bool:
    raw = (url or "").strip()
    netloc = _first_party_netloc()
    if not raw or not netloc:
        return False
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return parsed.netloc.lower() == netloc


def _is_already_applink(url: str) -> bool:
    raw = (url or "").strip()
    return raw.startswith(_APPLINK_PREFIXES)


def open_mode_for_url(url: str) -> OpenMode:
    path = urlsplit((url or "").strip()).path or ""
    for prefix in _window_path_prefixes():
        if path == prefix or path.startswith(prefix + "/"):
            return "window"
    return "sidebar"


def wrap_first_party_url_for_im(url: str, platform: str) -> str:
    """把自家 HTTPS 转为对应 IM 协议；外链 / 非 http(s) / 已包装地址原样返回。"""
    raw = (url or "").strip()
    if not raw or _is_already_applink(raw) or not is_first_party_url(raw):
        return url

    mode = open_mode_for_url(raw)
    encoded = quote(raw, safe="")
    platform_key = (platform or "").strip().lower()

    if platform_key == PLATFORM_DINGTALK:
        # 官方消息链接：pc_slide=true 侧栏；false 系统浏览器（钉钉无飞书 window 对等能力）
        pc_slide = "true" if mode == "sidebar" else "false"
        return f"dingtalk://dingtalkclient/page/link?url={encoded}&pc_slide={pc_slide}"
    if platform_key == PLATFORM_LARK:
        applink_host = "applink.larksuite.com"
    elif platform_key == PLATFORM_FEISHU:
        applink_host = "applink.feishu.cn"
    else:
        return url
    # web_url/open 侧栏枚举是 sidebar-semi；sidebar 仅用于 web_app/open
    feishu_mode = "sidebar-semi" if mode == "sidebar" else "window"
    return f"https://{applink_host}/client/web_url/open?mode={feishu_mode}&url={encoded}"


def rewrite_first_party_urls_in_text(text: str, platform: str) -> str:
    """改写 Markdown href，以及整段字符串本身就是自家 URL 的字段（如卡片按钮）。"""
    if not text or not isinstance(text, str):
        return text

    def _replace_md(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        return f"[{label}]({wrap_first_party_url_for_im(href, platform)})"

    rewritten = _MD_LINK_RE.sub(_replace_md, text)
    if rewritten != text:
        return rewritten

    stripped = text.strip()
    wrapped = wrap_first_party_url_for_im(stripped, platform)
    if wrapped == stripped:
        return text
    return text.replace(stripped, wrapped, 1)


def rewrite_im_content_urls(content: Any, platform: str) -> Any:
    """递归改写消息 / 卡片变量中的自家链接，不原地修改传入对象。"""
    if isinstance(content, str):
        return rewrite_first_party_urls_in_text(content, platform)
    if isinstance(content, dict):
        return {key: rewrite_im_content_urls(value, platform) for key, value in content.items()}
    if isinstance(content, list):
        return [rewrite_im_content_urls(item, platform) for item in content]
    if isinstance(content, tuple):
        return tuple(rewrite_im_content_urls(item, platform) for item in content)
    return content
