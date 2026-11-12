"""企微 bot template_card text_notice 共用组装。

链接分组：
- `jump_list`：主入口蓝链（≤3），无灰字
- `horizontal_content_list`：次要入口，左灰 keyname + 右蓝 value（≤6），文案分开避免重复

horizontal 入参支持：
- `(label, url)`：自动拆成 key/value
- `(keyname, value, url)`：显式指定
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional, Sequence, Union

JUMP_LIST_MAX = 3
HORIZONTAL_LINK_MAX = 6

HorizontalLink = Union[tuple[str, str], tuple[str, str, str]]

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_EMPHASIS_RE = re.compile(r"[*_`]+")
_COUNT_SUFFIX_RE = re.compile(r"^(.+?)（(\d+)）$")

# 常见入口：左短标签 / 右动作说明（避免 key==value）
_LABEL_KEY_VALUE: dict[str, tuple[str, str]] = {
    "本周待完成": ("本周任务", "待完成列表"),
    "本周已拜访": ("本周拜访", "已录入记录"),
    "团队待完成": ("团队任务", "待完成列表"),
    "团队已拜访": ("团队拜访", "已录入记录"),
    "跟进详情": ("跟进", "打开详情页"),
    "完整复盘报告": ("复盘", "打开完整报告"),
    "关键发现": ("关键发现", "查看详情"),
    "下周待确认": ("下周确认", "查看候选"),
    "待办候选": ("待办", "查看候选"),
    "经营事项": ("经营", "查看事项"),
    "查看": ("详情", "打开页面"),
}


def plain_text(text: str) -> str:
    """去掉 markdown 标记与裸链，保留可读正文。"""
    cleaned = _MD_LINK_RE.sub(r"\1", text or "")
    cleaned = _MD_EMPHASIS_RE.sub("", cleaned)
    cleaned = cleaned.replace(">", "").strip()
    return cleaned


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def split_horizontal_label(label: str) -> tuple[str, str]:
    """把单一文案拆成 (keyname, value)，尽量不重复。"""
    raw = _text(label)
    if not raw:
        return ("链接", "打开")
    counted = _COUNT_SUFFIX_RE.fullmatch(raw)
    if counted:
        base, count = counted.group(1), counted.group(2)
        mapped = _LABEL_KEY_VALUE.get(base)
        if mapped:
            return (mapped[0][:8], f"{count}条")
        return (base[:8], f"{count}条")
    mapped = _LABEL_KEY_VALUE.get(raw)
    if mapped:
        return (mapped[0][:8], mapped[1][:32])
    if len(raw) <= 4:
        return (raw, "打开")
    # 长文案：左侧截短标签，右侧保留完整说明
    return (raw[:4], raw[:32])


def _jump_items(
    candidates: Sequence[tuple[str, str]],
    *,
    limit: int = JUMP_LIST_MAX,
) -> list[dict[str, Any]]:
    jumps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, url in candidates:
        label = _text(label)
        url = _text(url)
        if not label or not url or url in seen:
            continue
        seen.add(url)
        jumps.append({"type": 1, "title": label[:16], "url": url})
        if len(jumps) >= limit:
            break
    return jumps


def _normalize_horizontal_item(
    item: HorizontalLink,
) -> Optional[tuple[str, str, str]]:
    if not item:
        return None
    if len(item) == 3:
        keyname, value, url = item[0], item[1], item[2]
    else:
        keyname, value = split_horizontal_label(item[0])
        url = item[1]
    keyname = _text(keyname)
    value = _text(value)
    url = _text(url)
    if not keyname or not value or not url:
        return None
    return (keyname[:8], value[:32], url)


def _horizontal_link_rows(
    links: Sequence[HorizontalLink],
    *,
    limit: int = HORIZONTAL_LINK_MAX,
    exclude_urls: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set(exclude_urls or ())
    if limit <= 0:
        return rows
    for item in links:
        normalized = _normalize_horizontal_item(item)
        if not normalized:
            continue
        keyname, value, url = normalized
        if url in seen:
            continue
        seen.add(url)
        rows.append(
            {
                "type": 1,
                "keyname": keyname,
                "value": value,
                "url": url,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def new_task_id(prefix: str = "tc") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def build_text_notice_card(
    *,
    title: str,
    desc: str = "",
    sub_title_text: str = "",
    jump_links: Sequence[tuple[str, str]] = (),
    horizontal_links: Sequence[HorizontalLink] = (),
    card_action_url: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """组装 text_notice。

    - jump_list：主入口蓝链，≤3
    - horizontal_content_list：次要入口（左灰右蓝），≤6；与 jump 同 url 去重
    - card_action：整卡点击，默认与主入口同链
    """
    jumps = _jump_items(list(jump_links), limit=JUMP_LIST_MAX)
    action = _text(card_action_url)
    if not action and jumps:
        action = jumps[0]["url"]
    if not action and horizontal_links:
        first = _normalize_horizontal_item(horizontal_links[0])
        if first:
            action = first[2]
    if not action:
        action = "https://work.weixin.qq.com"
    if not jumps:
        jumps = [{"type": 1, "title": "打开", "url": action}]

    jump_urls = {item["url"] for item in jumps}
    horizontal = _horizontal_link_rows(
        list(horizontal_links),
        limit=HORIZONTAL_LINK_MAX,
        exclude_urls=jump_urls,
    )
    card: dict[str, Any] = {
        "card_type": "text_notice",
        "main_title": {
            "title": _text(title) or "通知",
            "desc": _text(desc) or "详情",
        },
        "card_action": {"type": 1, "url": action},
        "jump_list": jumps,
        "task_id": _text(task_id) or new_task_id(),
    }
    body = plain_text(sub_title_text)
    if body:
        card["sub_title_text"] = body[:512]
    if horizontal:
        card["horizontal_content_list"] = horizontal
    return card


def extract_markdown_links(text: str) -> list[tuple[str, str]]:
    """按出现顺序提取 markdown 超链，同 url 去重。"""
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, url in _MD_LINK_RE.findall(text or ""):
        label = _text(label) or "查看详情"
        url = _text(url)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append((label, url))
    return links


def build_link_notice_card(
    *,
    title: str,
    body: str = "",
    links: Sequence[tuple[str, str]] = (),
    desc: str = "通知",
    task_prefix: str = "nt",
) -> dict[str, Any]:
    """评论 / 提醒类：首条进 jump（保留原标签），其余进 horizontal。"""
    link_list = [
        (_text(label), _text(url))
        for label, url in links
        if _text(label) and _text(url)
    ]
    action = link_list[0][1] if link_list else ""
    jump_links = link_list[:1]
    horizontal_links = link_list[1:]
    return build_text_notice_card(
        title=title,
        desc=desc,
        sub_title_text=body,
        jump_links=jump_links,
        horizontal_links=horizontal_links,
        card_action_url=action,
        task_id=new_task_id(task_prefix),
    )


def build_link_notice_from_markdown(
    content: str,
    *,
    title: str = "",
    desc: str = "通知",
    task_prefix: str = "md",
) -> dict[str, Any]:
    """把带 `[文案](url)` 的 markdown 转成 text_notice。"""
    raw = content or ""
    links = extract_markdown_links(raw)
    plain = plain_text(raw)
    header = _text(title)
    body = plain
    if not header:
        first, _, rest = plain.partition("\n")
        header = first.strip() or "通知"
        body = rest.strip()
    elif plain.startswith(header):
        body = plain[len(header) :].lstrip("\n").strip()
    return build_link_notice_card(
        title=header,
        body=body,
        links=links,
        desc=desc,
        task_prefix=task_prefix,
    )
