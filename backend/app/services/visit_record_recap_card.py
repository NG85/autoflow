"""拜访轻量复盘卡：动态 markdown，不走原 fill 模板链。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

from app.services.today_highlights_format import CARD_LINK_SEPARATOR
from app.services.visit_record_insight_reader import (
    RECAP_TAB_BUSINESS,
    RECAP_TAB_PERFORMANCE,
    RECAP_PAGE_TABS,
)
from app.utils.push_page_urls import (
    build_visit_record_page_url,
    build_visit_record_recap_page_url,
)

_TAB_TITLE = dict(RECAP_PAGE_TABS)

DINGTALK_PARAGRAPH_GAP = "\u00a0"
_RECAP_BODY_MAX_CHARS = 800
_TITLE_MAX_LEN = 50
_REVISED_NOTICE = "【修改后】"
_VISIT_DETAIL_LABEL = "跟进详情"
_RECAP_REPORT_LABEL = "完整复盘报告"
_EMPTY_MARKERS = frozenset({"--", "-", "—", "－"})

# 飞书卡片头色板缺省是 blue，复盘卡不再按 severity 换色。
_DEFAULT_HEADER_TEMPLATE = "blue"
_QUALITY_BY_SEVERITY = {
    "low": "正常",
    "medium": "留意",
    "high": "需关注",
}


@dataclass(frozen=True)
class RecapLiteCard:
    title: str
    header_template: str
    feishu_body: str
    dingtalk_text: str
    # 飞书复盘摘要（与 meta 分块，间距与操作区一致）。
    feishu_summary: str = ""
    # 飞书操作区：跟进详情 / 完整复盘蓝链，渲染在抽取按钮之前。
    feishu_action_links: str = ""
    # 飞书抽取入口：(文案, url)；空则不渲染。钉钉仍用 markdown 链接写在 dingtalk_text。
    feishu_extract_actions: tuple[tuple[str, str], ...] = ()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _display_text(value: Any) -> str:
    """空值和占位符（如 --）视为没有内容。"""
    text = _text(value)
    if text in _EMPTY_MARKERS:
        return ""
    return text


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _insight_severity(insight: Any) -> str:
    if insight is None:
        return ""
    if isinstance(insight, Mapping):
        return _text(insight.get("severity"))
    return _text(getattr(insight, "severity", None))


def resolve_recap_quality(insight: Any = None) -> tuple[str, str]:
    """标题质量只看 insight.severity：LOW/MEDIUM/HIGH → 正常/留意/需关注。

    第二个返回值是卡片头颜色，固定为默认蓝，不随 severity 变化。
    """
    severity = _insight_severity(insight)
    quality = _QUALITY_BY_SEVERITY.get(severity.lower(), "") if severity else ""
    return quality, _DEFAULT_HEADER_TEMPLATE


_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


def _parse_datetime_like(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("T", " ").replace("Z", "")
    if "+" in normalized[10:]:
        normalized = normalized.split("+", 1)[0]
    normalized = normalized.strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(normalized[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_visit_date_short(value: Any) -> str:
    parsed = _parse_datetime_like(value)
    if parsed:
        return f"{parsed.month}月{parsed.day}日"
    text = _text(value)
    return text


def format_entry_time(value: Any) -> str:
    parsed = _parse_datetime_like(value)
    if parsed:
        if parsed.hour or parsed.minute or parsed.second:
            return f"{parsed.month}月{parsed.day}日 {parsed.hour:02d}:{parsed.minute:02d}"
        return f"{parsed.month}月{parsed.day}日"
    return _text(value)


def compose_recap_title(quality: str, visit_date: str, account: str, *, max_len: int = _TITLE_MAX_LEN) -> str:
    parts = [part for part in (quality, visit_date) if part]
    prefix = " · ".join(parts)
    account = _text(account)
    if not prefix:
        return (account or "跟进记录")[:max_len]
    if not account:
        return prefix[:max_len]
    room = max_len - len(prefix) - 3
    if room <= 1:
        return prefix[:max_len]
    if len(account) > room:
        account = account[: max(1, room - 1)] + "…"
    return f"{prefix} · {account}"


def _truncate_recap(text: str) -> str:
    cleaned = "\n".join(line.rstrip() for line in (text or "").splitlines()).strip()
    if len(cleaned) <= _RECAP_BODY_MAX_CHARS:
        return cleaned
    return cleaned[: _RECAP_BODY_MAX_CHARS - 1].rstrip() + "…"


def _insight_recap_text(insight: Any) -> str:
    if insight is None:
        return ""
    recap_text = getattr(insight, "recap_text", None)
    if callable(recap_text):
        return _text(recap_text())
    if isinstance(insight, Mapping):
        return _first_text(
            insight.get("summary"),
            insight.get("title"),
            insight.get("detail_text"),
        )
    return _first_text(
        getattr(insight, "summary", None),
        getattr(insight, "title", None),
        getattr(insight, "detail_text", None),
    )


def _insight_field(insight: Any, name: str) -> Any:
    if insight is None:
        return None
    if isinstance(insight, Mapping):
        return insight.get(name)
    return getattr(insight, name, None)


def performance_summary_text(insight: Any) -> str:
    """表现评价只取 summary。空值和占位符视为没有评价。"""
    return _truncate_recap(_display_text(_insight_field(insight, "summary")))


def compose_recap_card_summary(
    recap_text: str,
    performance_text: str = "",
    *,
    dingtalk: bool = False,
) -> str:
    """有表现评价摘要时，业务推进和销售表现各成一行小标题，正文跟在下面。

    飞书用四级标题。钉钉 sampleMarkdown 不认四级标题，且会折叠空行，
    所以钉钉用二级标题，两段之间用不断行空格隔开。
    """
    if not performance_text:
        return recap_text or "--"
    business = _TAB_TITLE[RECAP_TAB_BUSINESS]
    performance = _TAB_TITLE[RECAP_TAB_PERFORMANCE]
    body = recap_text or "--"
    if dingtalk:
        return (
            f"## {business}\n{body}\n"
            f"{DINGTALK_PARAGRAPH_GAP}\n"
            f"## {performance}\n{performance_text}"
        )
    return (
        f"#### {business}\n\n"
        f"{body}\n\n"
        f"#### {performance}\n\n"
        f"{performance_text}"
    )


def resolve_recap_body_text(
    visit_record: Optional[Mapping[str, Any]],
    insight: Any = None,
) -> str:
    """轻量卡正文：优先 Aldebaran 洞察 summary，其次拜访记录上的复盘字段。"""
    from_insight = _truncate_recap(_insight_recap_text(insight))
    if from_insight:
        return from_insight
    record = visit_record or {}
    return _truncate_recap(
        _first_text(
            record.get("recap"),
            record.get("recap_summary"),
            record.get("analysis_recap"),
            record.get("followup_record"),
            record.get("followup_content"),
        )
    )


def resolve_account_name(visit_record: Optional[Mapping[str, Any]]) -> str:
    record = visit_record or {}
    return _first_text(
        record.get("followup_object_name"),
        record.get("account_name"),
        record.get("partner_name"),
    )


def _meta_lines(
    *,
    recorder_name: str,
    entry_time: str,
    opportunity: str,
) -> list[str]:
    lines: list[str] = []
    if recorder_name:
        lines.append(f"跟进人：{recorder_name}")
    if entry_time:
        lines.append(f"创建时间：{entry_time}")
    if opportunity:
        lines.append(f"商机：{opportunity}")
    return lines


def _primary_action_lines(
    *,
    visit_detail_url: str = "",
    report_url: str = "",
) -> list[str]:
    """详情 / 完整复盘等主操作链接，集中放在抽取按钮前。"""
    lines: list[str] = []
    if visit_detail_url:
        lines.append(f"[{_VISIT_DETAIL_LABEL}]({visit_detail_url})")
    if report_url:
        lines.append(f"[{_RECAP_REPORT_LABEL}]({report_url})")
    return lines


def format_extract_card_actions(
    card_links: Optional[Sequence[Mapping[str, Any]]],
    *,
    record_id: str,
) -> list[tuple[str, str]]:
    """有数据的 card_links → (文案, url)。count=1 不展示数量。"""
    from app.utils.push_page_urls import build_visit_record_extract_section_url

    actions: list[tuple[str, str]] = []
    for link in card_links or []:
        if not isinstance(link, Mapping):
            continue
        key = _text(link.get("key"))
        title = _text(link.get("title")) or key
        if not key or not title:
            continue
        url = build_visit_record_extract_section_url(record_id, key)
        if not url:
            continue
        count = link.get("count")
        label = f"{title}（{count}）" if isinstance(count, int) and count > 1 else title
        actions.append((label, url))
    return actions


def format_extract_card_lines(
    card_links: Optional[Sequence[Mapping[str, Any]]],
    *,
    record_id: str,
) -> list[str]:
    """钉钉等纯文本渠道：markdown 蓝链列表，由调用方用 CARD_LINK_SEPARATOR 拼成一行。"""
    return [
        f"[{label}]({url})"
        for label, url in format_extract_card_actions(
            card_links,
            record_id=record_id,
        )
    ]


def _format_extract_section(extract_lines: Optional[list[str]]) -> str:
    if not extract_lines:
        return ""
    return CARD_LINK_SEPARATOR.join(extract_lines)


def _feishu_meta_body(
    *,
    is_revised: bool,
    meta_lines: list[str],
) -> str:
    blocks: list[str] = []
    if is_revised:
        blocks.append(_REVISED_NOTICE)
    if meta_lines:
        blocks.append("\n".join(meta_lines))
    return "\n\n".join(blocks)


def _dingtalk_text(
    *,
    title: str,
    is_revised: bool,
    meta_lines: list[str],
    recap_text: str,
    action_lines: Optional[list[str]] = None,
    extract_lines: Optional[list[str]] = None,
) -> str:
    parts: list[str] = [f"### {title}"]
    if is_revised:
        parts.extend([DINGTALK_PARAGRAPH_GAP, _REVISED_NOTICE])
    if meta_lines:
        parts.extend([DINGTALK_PARAGRAPH_GAP, "\n".join(meta_lines)])
    parts.extend([DINGTALK_PARAGRAPH_GAP, recap_text or "--"])
    if action_lines:
        parts.extend([DINGTALK_PARAGRAPH_GAP, CARD_LINK_SEPARATOR.join(action_lines)])
    extract_section = _format_extract_section(extract_lines)
    if extract_section:
        parts.extend([DINGTALK_PARAGRAPH_GAP, extract_section])
    return "\n".join(parts)


def build_recap_lite_card(
    record_id: str,
    visit_record: Optional[Mapping[str, Any]] = None,
    *,
    recorder_name: Optional[str] = None,
    is_revised: bool = False,
    insight: Any = None,
    extract_links: Optional[Sequence[Mapping[str, Any]]] = None,
    performance_insight: Any = None,
) -> RecapLiteCard:
    record = visit_record or {}
    performance_text = performance_summary_text(performance_insight)
    quality, header_template = resolve_recap_quality(insight)
    visit_date = format_visit_date_short(
        record.get("visit_communication_date") or record.get("last_modified_time")
    )
    account = resolve_account_name(record)
    title = compose_recap_title(quality, visit_date, account)
    entry_time = format_entry_time(record.get("last_modified_time") or record.get("visit_communication_date"))
    opportunity = _display_text(record.get("opportunity_name"))
    recap_text = resolve_recap_body_text(record, insight=insight)
    feishu_summary = compose_recap_card_summary(recap_text, performance_text)
    dingtalk_summary = compose_recap_card_summary(
        recap_text,
        performance_text,
        dingtalk=True,
    )
    visit_detail_url = build_visit_record_page_url(record_id)
    detail_url = build_visit_record_recap_page_url(record_id)
    recorder = _text(recorder_name) or _text(record.get("recorder"))
    extract_actions = format_extract_card_actions(
        extract_links,
        record_id=record_id,
    )
    extract_lines = [f"[{label}]({url})" for label, url in extract_actions]
    meta_lines = _meta_lines(
        recorder_name=recorder,
        entry_time=entry_time,
        opportunity=opportunity,
    )
    action_lines = _primary_action_lines(
        visit_detail_url=visit_detail_url,
        report_url=detail_url,
    )

    feishu_body = _feishu_meta_body(
        is_revised=is_revised,
        meta_lines=meta_lines,
    )
    dingtalk_text = _dingtalk_text(
        title=title,
        is_revised=is_revised,
        meta_lines=meta_lines,
        recap_text=dingtalk_summary,
        action_lines=action_lines,
        extract_lines=extract_lines,
    )
    return RecapLiteCard(
        title=title,
        header_template=header_template,
        feishu_body=feishu_body,
        dingtalk_text=dingtalk_text,
        feishu_summary=feishu_summary,
        feishu_action_links=CARD_LINK_SEPARATOR.join(action_lines),
        feishu_extract_actions=tuple(extract_actions),
    )
