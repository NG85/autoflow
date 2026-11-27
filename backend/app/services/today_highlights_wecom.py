"""今日重点企微 template_card（text_notice）。

jump_list：主入口「查看/更多」。
horizontal：业务入口（关键发现、本周任务等），左灰短标签 + 右蓝说明。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from app.platforms.wecom.text_notice import (
    HORIZONTAL_LINK_MAX,
    JUMP_LIST_MAX,
    build_text_notice_card,
    new_task_id,
    plain_text,
)
from app.services.company_today_highlights import CXO_CARD_TITLE, render_card_plain_text
from app.services.department_today_highlights import (
    DAILY_REPORT_LINK_LABEL,
    INTERVENTION_TITLE,
    TEAM_CARD_TITLE,
    WEEKLY_REPORT_LINK_LABEL,
    _dingtalk_item_lines as _team_item_lines,
    _section_exit_link as _team_section_exit_link,
    _team_tail_lines,
    _watch_section,
    card_footer_buttons,
)
from app.services.sales_today_highlights import (
    SALES_CARD_TITLE,
    _dingtalk_item_lines as _sales_item_lines,
    _section_by_key,
    _section_exit_link as _sales_section_exit_link,
    _text,
    card_action_buttons,
    card_blue_links,
)
from app.utils.push_page_urls import (
    build_company_highlights_url,
    build_department_highlights_url,
    build_sales_today_highlights_url,
)

__all__ = [
    "HORIZONTAL_LINK_MAX",
    "JUMP_LIST_MAX",
    "build_cxo_wecom_template_card",
    "build_sales_wecom_template_card",
    "build_team_wecom_template_card",
    "build_text_notice_card",
]

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _wecom_reason_line(raw: str) -> str:
    """企微没有引用栏。引用行去掉符号后加「原因：」。"""
    text = (raw or "").strip()
    quoted = text.startswith(">")
    plain = plain_text(text)
    if not plain:
        return ""
    if quoted:
        return f"原因：{plain}"
    return plain


_JUMP_REPORT_TITLE = {
    DAILY_REPORT_LINK_LABEL: "昨日日报",
    WEEKLY_REPORT_LINK_LABEL: "上周周报",
}


def _team_jump_and_horizontal(
    report_links: list[tuple[str, str]],
    footer: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """跳转只放本日。横栏先放本日报告，再放本周入口。

    调用方把「查看 / 更多」放在跳转最前，这里再接昨日日报或上周周报。
    本周待完成、已拜访、例会留在横栏，排在报告后面。周一的「团队上周拜访」
    由调用方接在这些入口后面。满员是 2 个跳转加 6 条横栏。
    """
    jump_tail: list[tuple[str, str]] = []
    reports: list[tuple[str, str]] = []
    for label, url in report_links:
        title = _JUMP_REPORT_TITLE.get(label)
        if title:
            jump_tail.append((title, url))
        else:
            reports.append((label, url))
    return jump_tail, [*reports, *footer]


def _parse_md_link(raw: str) -> Optional[tuple[str, str]]:
    match = _MD_LINK_RE.fullmatch((raw or "").strip())
    if not match:
        return None
    return (match.group(1), match.group(2))


def build_sales_wecom_template_card(
    assembled: Mapping[str, Any],
    *,
    owner_name: str = "",
) -> dict[str, Any]:
    """销售今日重点企微卡。

    jump：查看 / 更多。
    horizontal：关键发现、下周待确认、本周待完成、本周已拜访。
    """
    day = assembled.get("report_date")
    title = f"{SALES_CARD_TITLE}｜{day}"
    section = _section_by_key(assembled, "immediate_today")
    items = list((section or {}).get("items") or [])
    body_lines: list[str] = []
    desc = "立即处理"
    if section and items:
        heading = _text(section.get("title"))
        if heading:
            desc = heading
        body_lines.extend(
            line
            for line in (
                _wecom_reason_line(raw)
                for raw in _sales_item_lines("immediate_today", items[0])
            )
            if line
        )

    exit_pair = _parse_md_link(_sales_section_exit_link(section)) if section else None
    blue = card_blue_links(assembled)
    week = card_action_buttons(assembled, owner_name=owner_name)

    jump_candidates: list[tuple[str, str]] = []
    if exit_pair:
        jump_candidates.append(exit_pair)

    action = ""
    if exit_pair:
        action = exit_pair[1]
    elif day:
        action = build_sales_today_highlights_url(str(day), "immediate_today") or ""

    return build_text_notice_card(
        title=title,
        desc=desc,
        sub_title_text="\n".join(line for line in body_lines if line)
        or "今天没有立即处理事项。",
        jump_links=jump_candidates,
        horizontal_links=[*blue, *week],
        card_action_url=action,
        task_id=new_task_id("th_s"),
    )


def build_team_wecom_template_card(
    assembled: Mapping[str, Any],
    *,
    owner_name: str = "",
) -> dict[str, Any]:
    """团队今日重点企微卡。

    jump：查看 / 更多，然后是昨日日报或上周周报。
    horizontal：重点商机、人效、经营趋势，然后是本周任务、拜访、例会。
    周一没有本周拜访，横栏在本周任务后面补上周拜访。
    """
    day = assembled.get("report_date")
    title = f"{TEAM_CARD_TITLE}｜{day}"
    sections = list(assembled.get("sections") or [])
    section = sections[0] if sections else None
    items = list((section or {}).get("items") or [])

    body_lines: list[str] = []
    desc = INTERVENTION_TITLE
    if section and items:
        body_lines.append(INTERVENTION_TITLE)
        body_lines.extend(
            line
            for line in (_wecom_reason_line(raw) for raw in _team_item_lines(items[0]))
            if line
        )
    watch_links: list[tuple[str, str]] = []
    watch = _watch_section(assembled, dingtalk=True)
    if watch:
        if body_lines:
            body_lines.append("")
        for line in watch.split("\n"):
            parsed = _parse_md_link(line)
            if parsed:
                watch_links.append(parsed)
                continue
            plain = plain_text(line)
            if plain:
                body_lines.append(plain)

    report_raw = _team_tail_lines(
        assembled,
        has_other_blocks=bool(items) or bool(watch),
        dingtalk=True,
    )
    report_links: list[tuple[str, str]] = []
    for line in report_raw:
        for part in re.split(r"\s*｜\s*", line):
            parsed = _parse_md_link(part.strip())
            if parsed:
                report_links.append(parsed)

    exit_pair = (
        _parse_md_link(_team_section_exit_link(section)) if section and items else None
    )
    footer = card_footer_buttons(assembled, owner_name=owner_name)
    seen_urls = {url for _, url in footer}
    week_links = [
        *footer,
        *[
            pair
            for pair in watch_links
            if pair[1] and pair[1] not in seen_urls
        ],
    ]
    # 空态只打开团队重点页（date），不指定 tab / department_id。
    default_page_url = build_department_highlights_url(str(day)) if day else ""

    jump_candidates: list[tuple[str, str]] = []
    if exit_pair:
        jump_candidates.append(exit_pair)
    elif default_page_url:
        jump_candidates.append(("查看", default_page_url))
    jump_tail, horizontal = _team_jump_and_horizontal(report_links, week_links)
    jump_candidates.extend(jump_tail)

    action = ""
    if exit_pair:
        action = exit_pair[1]
    elif default_page_url:
        action = default_page_url

    if not body_lines and report_links:
        body_lines.append("今天没有需介入事项，可以先看这些报告。")

    return build_text_notice_card(
        title=title,
        desc=desc if (section and items) else "团队报告",
        sub_title_text="\n".join(line for line in body_lines if line)
        or "今天没有需介入事项。",
        jump_links=jump_candidates,
        horizontal_links=horizontal,
        card_action_url=action,
        task_id=new_task_id("th_t"),
    )


def build_cxo_wecom_template_card(assembled: Mapping[str, Any]) -> dict[str, Any]:
    """CXO 今日重点企微卡。正文是各报告 summary，两份报告都进 jump_list，整卡打开第一份。"""
    day = assembled.get("report_date")
    title = f"{CXO_CARD_TITLE}｜{day}"
    jumps = [
        (_text(item.get("label")), _text(item.get("url")))
        for item in (assembled.get("report_entries") or [])
        if isinstance(item, Mapping) and _text(item.get("label")) and _text(item.get("url"))
    ]
    action = jumps[0][1] if jumps else (build_company_highlights_url(str(day)) if day else "")
    return build_text_notice_card(
        title=title,
        desc="经营阅读",
        sub_title_text=render_card_plain_text(assembled),
        jump_links=jumps,
        horizontal_links=[],
        card_action_url=action,
        task_id=new_task_id("th_c"),
    )
