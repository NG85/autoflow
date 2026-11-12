"""今日重点企微 template_card 组装。"""

from app.platforms.wecom.text_notice import split_horizontal_label
from app.services.today_highlights_wecom import (
    HORIZONTAL_LINK_MAX,
    JUMP_LIST_MAX,
    build_team_wecom_template_card,
    build_text_notice_card,
)


def test_split_horizontal_label_avoids_duplicate_copy():
    assert split_horizontal_label("关键发现（2）") == ("关键发现", "2条")
    assert split_horizontal_label("本周待完成") == ("本周任务", "待完成列表")
    assert split_horizontal_label("本周已拜访") == ("本周拜访", "已录入记录")
    assert split_horizontal_label("跟进详情") == ("跟进", "打开详情页")


def test_text_notice_groups_jump_and_horizontal_with_distinct_key_value():
    card = build_text_notice_card(
        title="我的今日重点｜2026-11-11",
        desc="立即处理",
        sub_title_text="先做这件事\n[查看](https://example.com/x)",
        jump_links=[("更多（2）", "https://example.com/a")],
        horizontal_links=[
            ("关键发现（2）", "https://example.com/b"),
            ("本周待完成", "https://example.com/c"),
            ("本周已拜访", "https://example.com/d"),
        ],
        card_action_url="https://example.com/a",
        task_id="t1",
    )
    assert card["jump_list"] == [
        {"type": 1, "title": "更多（2）", "url": "https://example.com/a"}
    ]
    assert card["card_action"]["url"] == "https://example.com/a"
    horizontal = card["horizontal_content_list"]
    assert len(horizontal) == 3
    assert horizontal[0] == {
        "type": 1,
        "keyname": "关键发现",
        "value": "2条",
        "url": "https://example.com/b",
    }
    assert horizontal[1]["keyname"] == "本周任务"
    assert horizontal[1]["value"] == "待完成列表"
    assert horizontal[1]["keyname"] != horizontal[1]["value"]
    assert "[查看]" not in card["sub_title_text"]


def test_horizontal_link_rows_cap_at_six():
    links = [(f"入口{i}", f"https://example.com/{i}") for i in range(1, 10)]
    card = build_text_notice_card(
        title="t",
        jump_links=[("查看", "https://example.com/view")],
        horizontal_links=links,
        card_action_url="https://example.com/view",
        task_id="t2",
    )
    assert len(card["jump_list"]) <= JUMP_LIST_MAX
    assert len(card["horizontal_content_list"]) == HORIZONTAL_LINK_MAX


def test_team_wecom_card_puts_reports_in_horizontal(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_task_list_page_url",
        lambda **kwargs: "https://example.com/team-task",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_week_visit_list_url",
        lambda *args, **kwargs: "https://example.com/team-visit",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_leader_meeting_review_list_url",
        lambda *args, **kwargs: "https://example.com/meeting",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_team_daily_report_url",
        lambda *args, **kwargs: "https://example.com/daily",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights._report_card_lines",
        lambda assembled, **kwargs: [
            "[团队重点商机](https://example.com/deal) ｜ [团队人效](https://example.com/eff)"
        ],
    )
    assembled = {
        "report_date": "2026-11-11",
        "department_name": "华东一区",
        "sections": [
            {
                "key": "leader_intervene",
                "title": "今日需介入",
                "url": "https://example.com/intervene",
                "items": [
                    {
                        "related_user_name": "张三",
                        "claim": "跟进 POC",
                        "leader_action": "今天对齐",
                        "department_name": "华东一区",
                    }
                ],
            }
        ],
        "report_entries": [],
        "visit_watch": None,
    }
    card = build_team_wecom_template_card(assembled, owner_name="王经理")
    assert card["card_type"] == "text_notice"
    assert card["jump_list"][0]["title"].startswith(("查看", "更多"))
    assert "张三" in card["sub_title_text"]
    assert "[" not in card["sub_title_text"]
    horizontal = card.get("horizontal_content_list") or []
    assert all(row.get("type") == 1 and row.get("url") for row in horizontal)
    assert all(row["keyname"] != row["value"] for row in horizontal)
    urls = {row["url"] for row in horizontal}
    assert "https://example.com/deal" in urls
    assert "https://example.com/team-task" in urls


def test_team_wecom_card_empty_state_jumps_to_date_only_page(monkeypatch):
    monkeypatch.setattr(
        "app.services.today_highlights_wecom.build_department_highlights_url",
        lambda day, department_id="", section="": (
            f"https://example.com/v2/department-highlights?date={day}"
        ),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.card_footer_buttons",
        lambda assembled, owner_name="": [],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights._team_tail_lines",
        lambda assembled, **kwargs: [
            "[团队日报](https://example.com/daily)",
        ],
    )
    assembled = {
        "report_date": "2026-11-11",
        "department_name": "华东一区",
        "sections": [
            {
                "key": "leader_intervene",
                "title": "今日需介入",
                "url": "https://example.com/intervene",
                "items": [],
            }
        ],
        "report_entries": [],
        "visit_watch": None,
    }
    card = build_team_wecom_template_card(assembled, owner_name="王经理")
    assert card["jump_list"] == [
        {
            "type": 1,
            "title": "查看",
            "url": "https://example.com/v2/department-highlights?date=2026-11-11",
        }
    ]
    assert card["card_action"]["url"].endswith("date=2026-11-11")
    assert "tab=" not in card["card_action"]["url"]
    assert "可以先看这些报告" in card["sub_title_text"]
