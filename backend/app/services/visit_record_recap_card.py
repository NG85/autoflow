"""拜访轻量复盘卡：动态 markdown，不走原 fill 模板链。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional

from app.utils.push_page_urls import build_visit_record_recap_page_url

DINGTALK_PARAGRAPH_GAP = "\u00a0"
_RECAP_BODY_MAX_CHARS = 800
_TITLE_MAX_LEN = 50
_REVISED_NOTICE = "【修改后】"

_QUALITY_BY_SEVERITY = {
    "low": ("正常", "green"),
    "medium": ("留意", "blue"),
    "high": ("需关注", "orange"),
}


@dataclass(frozen=True)
class RecapLiteCard:
    title: str
    header_template: str
    feishu_body: str
    dingtalk_text: str


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


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
    """复盘质量只看 insight.severity：LOW/MEDIUM/HIGH → 正常/留意/需关注。"""
    severity = _insight_severity(insight)
    if not severity:
        return "", "blue"
    mapped = _QUALITY_BY_SEVERITY.get(severity.lower())
    if mapped:
        return mapped
    return "", "blue"


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
    narrow: bool,
) -> list[str]:
    items: list[str] = []
    if recorder_name:
        items.append(f"记录人：{recorder_name}")
    if entry_time:
        items.append(f"跟进时间：{entry_time}")
    if opportunity:
        items.append(f"商机：{opportunity}")
    if not items:
        return []
    if narrow:
        return items
    return [" · ".join(items)]


def _feishu_body(
    *,
    is_revised: bool,
    meta_lines: list[str],
    recap_text: str,
    detail_url: str,
) -> str:
    blocks: list[str] = []
    if is_revised:
        blocks.append(_REVISED_NOTICE)
    if meta_lines:
        blocks.append("\n".join(meta_lines))
    blocks.append(recap_text or "--")
    if detail_url:
        blocks.append(f"[查看详情]({detail_url})")
    return "\n\n".join(blocks)


def _dingtalk_text(
    *,
    title: str,
    is_revised: bool,
    meta_lines: list[str],
    recap_text: str,
    detail_url: str,
) -> str:
    parts: list[str] = [f"### {title}"]
    if is_revised:
        parts.extend([DINGTALK_PARAGRAPH_GAP, _REVISED_NOTICE])
    if meta_lines:
        parts.extend([DINGTALK_PARAGRAPH_GAP, "\n".join(meta_lines)])
    parts.extend([DINGTALK_PARAGRAPH_GAP, recap_text or "--"])
    if detail_url:
        parts.extend([DINGTALK_PARAGRAPH_GAP, f"[查看详情]({detail_url})"])
    return "\n".join(parts)


def build_recap_lite_card(
    record_id: str,
    visit_record: Optional[Mapping[str, Any]] = None,
    *,
    recorder_name: Optional[str] = None,
    recap_detail_query: str = "panel=recap",
    is_revised: bool = False,
    insight: Any = None,
) -> RecapLiteCard:
    record = visit_record or {}
    quality, header_template = resolve_recap_quality(insight)
    visit_date = format_visit_date_short(
        record.get("visit_communication_date") or record.get("last_modified_time")
    )
    account = resolve_account_name(record)
    title = compose_recap_title(quality, visit_date, account)
    entry_time = format_entry_time(record.get("last_modified_time") or record.get("visit_communication_date"))
    opportunity = _text(record.get("opportunity_name"))
    recap_text = resolve_recap_body_text(record, insight=insight)
    detail_url = build_visit_record_recap_page_url(record_id, query=recap_detail_query)
    recorder = _text(recorder_name) or _text(record.get("recorder"))

    feishu_body = _feishu_body(
        is_revised=is_revised,
        meta_lines=_meta_lines(
            recorder_name=recorder,
            entry_time=entry_time,
            opportunity=opportunity,
            narrow=False,
        ),
        recap_text=recap_text,
        detail_url=detail_url,
    )
    dingtalk_text = _dingtalk_text(
        title=title,
        is_revised=is_revised,
        meta_lines=_meta_lines(
            recorder_name=recorder,
            entry_time=entry_time,
            opportunity=opportunity,
            narrow=True,
        ),
        recap_text=recap_text,
        detail_url=detail_url,
    )
    return RecapLiteCard(
        title=title,
        header_template=header_template,
        feishu_body=feishu_body,
        dingtalk_text=dingtalk_text,
    )
