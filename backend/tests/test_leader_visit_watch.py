"""负责人周一、周五拜访对照：窗口、点名规则、卡片正文。"""

from datetime import date

from app.services.department_today_highlights import (
    card_footer_buttons,
    render_card_markdown,
    render_dingtalk_markdown,
)
from app.services.leader_visit_watch import select_visit_watch, visit_watch_lines, visit_watch_window


MONDAY = date(2026, 10, 12)
FRIDAY = date(2026, 10, 16)
THURSDAY = date(2026, 10, 15)


def test_window_is_last_week_on_monday_and_this_week_on_friday():
    last = visit_watch_window(MONDAY)
    current = visit_watch_window(FRIDAY)
    assert last == (date(2026, 10, 5), date(2026, 10, 11), "上周")
    assert current == (date(2026, 10, 12), date(2026, 10, 18), "本周")
    assert visit_watch_window(THURSDAY) is None


def test_flags_zero_or_under_half_and_high_above_average():
    picked = select_visit_watch(
        [
            {"name": "张三", "visit_total": 6, "high_count": 0},
            {"name": "李四", "visit_total": 4, "high_count": 1},
            {"name": "王五", "visit_total": 0, "high_count": 3},
            {"name": "赵六", "visit_total": 1, "high_count": 0},
        ]
    )
    assert [item["name"] for item in picked["low_entry"]] == ["王五"]
    assert picked["high_severity"] == [{"name": "王五", "high_count": 3}]


def test_a_small_gap_above_average_is_not_named():
    picked = select_visit_watch(
        [
            {"name": "张三", "visit_total": 4, "high_count": 1},
            {"name": "李四", "visit_total": 4, "high_count": 1},
            {"name": "王五", "visit_total": 1, "high_count": 2},
        ]
    )
    assert picked == {"low_entry": [], "high_severity": []}


def test_all_zero_visits_and_even_high_counts_are_not_named():
    picked = select_visit_watch(
        [
            {"name": "张三", "visit_total": 0, "high_count": 2},
            {"name": "李四", "visit_total": 0, "high_count": 2},
        ]
    )
    assert picked == {"low_entry": [], "high_severity": []}


def test_card_appends_watch_after_the_exit_link():
    assembled = {
        "report_date": "2026-10-16",
        "sections": [
            {
                "key": "leader_intervene",
                "title": "今日需介入",
                "items": [{"claim": "拍板", "related_user_name": "张三"}],
                "url": "https://example.test/v2/department-highlights?date=2026-10-16&tab=leader_intervene",
            }
        ],
        "visit_watch": {
            "label": "本周",
            "week_start": "2026-10-12",
            "week_end": "2026-10-18",
            "visit_total": 11,
            "recap_total": 8,
            "recap_by_severity": [
                {"severity": "HIGH", "count": 3},
                {"severity": "MEDIUM", "count": 4},
                {"severity": "LOW", "count": 1},
            ],
            "low_entry": [{"name": "王五", "visit_total": 0}],
            "high_severity": [{"name": "赵六", "high_count": 2}],
            "url": "https://example.test/v2/behavior?visit_communication_date_start=2026-10-12&visit_communication_date_end=2026-10-18",
        },
    }
    body = render_card_markdown(assembled)
    assert body.index("[查看]") < body.index("**本周拜访**")
    assert "10月12日" not in body
    assert "拜访 11 条，其中需关注 3 条" in body
    assert "留意" not in body
    assert "正常" not in body
    assert body.index("拜访 11 条") < body.index("拜访偏少：王五 0 条")
    assert "拜访偏少：王五 0 条" in body
    assert "需关注偏多：赵六 2 条" in body
    assert body.index("需关注偏多：赵六 2 条") < body.index("[查看本周拜访](")
    assert "本周已拜访" not in body
    dingtalk = render_dingtalk_markdown(assembled, title="今日重点｜2026-10-16")
    assert "## 本周拜访" in dingtalk
    assert "- 王五" not in dingtalk
    assert "本周已拜访" not in dingtalk
    assert card_footer_buttons(assembled) == []


def test_team_stats_stay_when_nobody_is_named():
    lines = visit_watch_lines(
        {
            "label": "本周",
            "week_start": "2026-10-12",
            "week_end": "2026-10-18",
            "visit_total": 6,
            "recap_by_severity": [
                {"severity": "HIGH", "count": 0},
                {"severity": "MEDIUM", "count": 2},
                {"severity": "LOW", "count": 4},
            ],
            "low_entry": [],
            "high_severity": [],
        }
    )
    assert "拜访 6 条，其中需关注 0 条" in lines
    assert not any("留意" in line or "正常" in line for line in lines)
    assert not any(line.startswith("拜访偏少") or line.startswith("需关注偏多") for line in lines)


def test_empty_watch_adds_no_lines():
    assert visit_watch_lines({"label": "本周", "low_entry": [], "high_severity": []}) == []
