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
from app.services.company_today_highlights import CXO_CARD_TITLE
from app.services.department_today_highlights import (
    INTERVENTION_TITLE,
    TEAM_CARD_TITLE,
    _dingtalk_item_lines as _team_item_lines,
    _other_people_line,
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
            plain_text(line)
            for line in _sales_item_lines("immediate_today", items[0])
            if plain_text(line)
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

    jump：查看 / 更多；无介入事项时落到团队重点页（仅 date）。
    horizontal：团队待完成 / 已拜访 / 报告入口。
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
            plain_text(line) for line in _team_item_lines(items[0]) if plain_text(line)
        )
        others = plain_text(_other_people_line(items))
        if others:
            body_lines.append(others)
    watch = _watch_section(assembled, dingtalk=True)
    if watch:
        if body_lines:
            body_lines.append("")
        for line in watch.split("\n"):
            if line.strip().startswith("["):
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
    # 空态只打开团队重点页（date），不指定 tab / department_id。
    default_page_url = build_department_highlights_url(str(day)) if day else ""

    jump_candidates: list[tuple[str, str]] = []
    if exit_pair:
        jump_candidates.append(exit_pair)
    elif default_page_url:
        jump_candidates.append(("查看", default_page_url))

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
        horizontal_links=[*footer, *report_links],
        card_action_url=action,
        task_id=new_task_id("th_t"),
    )


def build_cxo_wecom_template_card(assembled: Mapping[str, Any]) -> dict[str, Any]:
    """CXO 今日重点企微卡。两份报告都进 jump_list，整卡打开第一份。"""
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
        sub_title_text="",
        jump_links=jumps,
        horizontal_links=[],
        card_action_url=action,
        task_id=new_task_id("th_c"),
    )
