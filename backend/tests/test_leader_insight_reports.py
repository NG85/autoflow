"""负责人今日重点的报告入口：按 oauth id 筛 USER 洞察、卡片蓝链。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.department_today_highlights import (
    REPORTS_ONLY_LEAD,
    assemble_department_highlights,
    render_card_markdown,
    render_dingtalk_markdown,
)
from app.services.leader_insight_reports import (
    ENTITY_USER,
    INSIGHT_BUSINESS_TREND,
    INSIGHT_KEY_OPPORTUNITIES,
    INSIGHT_PERSONA,
    LEADER_REPORT_SLOTS,
    LeaderReportSlot,
    load_leader_insight_reports,
    load_leader_insight_sections,
    pick_report_entries,
    pick_report_sections,
    report_entry_lines,
    report_period,
)

PERSONA_TYPE = "LEADER_EFFICIENCY_INSIGHT"
KEY_DEAL_REVIEW = "KEY_DEAL_REVIEW"
TEAM_TREND = "TEAM_OPERATING_TREND"
OAUTH_ID = "oauth-leader"


MONDAY = date(2026, 10, 12)
TUESDAY = date(2026, 10, 13)
FRIDAY = date(2026, 10, 16)


def _row(
    *,
    entity_type=ENTITY_USER,
    entity_id=OAUTH_ID,
    insight_type=PERSONA_TYPE,
    period_key="2026-10-13",
    generated_at="2026-10-13T08:00:00",
    unique_id="insight-1",
    title="人效摘要",
    summary="人均产出",
    detail_text="详情",
    evidence='["拜访"]',
    severity="MEDIUM",
    category=None,
    id=1,
):
    return SimpleNamespace(
        entity_type=entity_type,
        entity_id=entity_id,
        insight_type=insight_type,
        period_key=period_key,
        generated_at=generated_at,
        unique_id=unique_id,
        title=title,
        summary=summary,
        detail_text=detail_text,
        evidence=evidence,
        severity=severity,
        category=category,
        id=id,
    )


def _slot(**overrides) -> LeaderReportSlot:
    values = dict(
        key="leader_efficiency_insight",
        title="团队人效",
        entity_type=ENTITY_USER,
        insight_types=(PERSONA_TYPE,),
        match_period=True,
    )
    values.update(overrides)
    return LeaderReportSlot(**values)


def test_report_period_matches_the_report_date():
    assert report_period(MONDAY) == "2026-10-12"
    assert report_period(TUESDAY) == "2026-10-13"
    assert report_period(FRIDAY) == "2026-10-16"


def test_empty_insight_types_do_not_appear():
    entries = pick_report_entries(
        [_row()],
        period_key="2026-10-13",
        slots=(
            _slot(key="key_deal_review", title="重点商机", insight_types=()),
            _slot(),
        ),
    )
    assert [item["key"] for item in entries] == ["leader_efficiency_insight"]
    assert entries[0]["label"] == "团队人效"


def test_picks_latest_matching_row_and_builds_highlights_urls(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    slots = (
        _slot(
            key="key_deal_review",
            title="重点商机",
            insight_types=INSIGHT_KEY_OPPORTUNITIES,
        ),
        _slot(insight_types=INSIGHT_PERSONA),
        _slot(
            key="team_operating_trend",
            title="经营趋势",
            insight_types=INSIGHT_BUSINESS_TREND,
        ),
    )
    rows = [
        _row(insight_type=PERSONA_TYPE, id=3),
        _row(
            insight_type=PERSONA_TYPE,
            generated_at="2026-10-12T08:00:00",
            id=2,
        ),
        _row(insight_type=KEY_DEAL_REVIEW, id=4),
        _row(insight_type=TEAM_TREND, id=5),
        _row(insight_type=PERSONA_TYPE, period_key="2026-10-12", id=6),
        _row(entity_type="DEPARTMENT", insight_type=PERSONA_TYPE, id=7),
    ]
    entries = pick_report_entries(rows, period_key="2026-10-13", slots=slots)
    assert entries == [
        {
            "key": "key_deal_review",
            "label": "重点商机",
            "url": "https://example.com/v2/department-highlights?date=2026-10-13&tab=key_deal_review",
        },
        {
            "key": "leader_efficiency_insight",
            "label": "团队人效",
            "url": "https://example.com/v2/department-highlights?date=2026-10-13&tab=leader_efficiency_insight",
        },
        {
            "key": "team_operating_trend",
            "label": "经营趋势",
            "url": "https://example.com/v2/department-highlights?date=2026-10-13&tab=team_operating_trend",
        },
    ]


def test_omits_entry_when_url_cannot_be_built(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_insight_reports.build_department_highlights_url",
        lambda *args, **kwargs: "",
    )
    entries = pick_report_entries(
        [_row()],
        period_key="2026-10-13",
        slots=(_slot(),),
    )
    assert entries == []


def test_report_entry_lines_join_in_slot_order():
    lines = report_entry_lines(
        [
            {"label": "重点商机", "url": "https://example.com/opp"},
            {"label": "团队人效", "url": "https://example.com/persona"},
            {"label": "经营趋势", "url": "https://example.com/trend"},
        ]
    )
    assert lines == [
        "[重点商机](https://example.com/opp)  "
        "[团队人效](https://example.com/persona)  "
        "[经营趋势](https://example.com/trend)"
    ]
    assert report_entry_lines([]) == []
    assert report_entry_lines([{"label": "人效", "url": ""}]) == []


def test_card_puts_report_links_after_visit_watch(monkeypatch):
    monkeypatch.setattr(
        "app.services.department_today_highlights.build_department_highlights_url",
        lambda *args, **kwargs: "https://example.com/dept",
    )
    assembled = assemble_department_highlights(
        [
            {
                "unique_id": "u1",
                "subject_type": "USER",
                "subject_id": OAUTH_ID,
                "audience": "leader",
                "status": "PENDING",
                "is_deleted": 0,
                "item_type": "TODAY_INTERVENTION",
                "extract_key": "u1",
                "card_links": [{"key": "leader_intervene", "title": "今日需介入"}],
                "payload": {
                    "claim": "拍板",
                    "leader_action": "今天确认",
                    "related_user_id": "oauth-zhang",
                },
            }
        ],
        TUESDAY,
        "dept-1",
        {"people": {"oauth-zhang": "张三"}, "accounts": {}, "opportunities": {}},
    )
    assembled["visit_watch"] = {
        "label": "本周",
        "visit_total": 3,
        "department_names": ["华北销售"],
        "low_entry": [],
        "high_severity": [],
        "url": "https://example.com/visits",
    }
    assembled["report_entries"] = [
        {"key": "leader_efficiency_insight", "label": "团队人效", "url": "https://example.com/persona"},
        {"key": "team_operating_trend", "label": "经营趋势", "url": "https://example.com/trend"},
    ]
    body = render_card_markdown(assembled)
    assert body.index("今日需介入") < body.index("本周拜访")
    assert body.index("本周拜访") < body.index("[团队人效](https://example.com/persona)")
    assert "[团队人效](https://example.com/persona)  [经营趋势](https://example.com/trend)" in body
    dingtalk = render_dingtalk_markdown(assembled, title="今日重点｜2026-10-13")
    assert dingtalk.index("## 本周拜访") < dingtalk.index("[团队人效](")
    assert REPORTS_ONLY_LEAD not in body
    assert REPORTS_ONLY_LEAD not in dingtalk


def test_reports_only_card_adds_a_lead_in():
    assembled = {
        "report_date": "2026-10-13",
        "sections": [],
        "report_entries": [
            {"key": "key_deal_review", "label": "重点商机", "url": "https://example.com/deals"},
        ],
    }
    body = render_card_markdown(assembled)
    assert body.startswith(REPORTS_ONLY_LEAD)
    assert "[重点商机](https://example.com/deals)" in body
    assert "今日需介入" not in body
    dingtalk = render_dingtalk_markdown(assembled, title="今日重点｜2026-10-13")
    assert REPORTS_ONLY_LEAD in dingtalk
    assert dingtalk.index(REPORTS_ONLY_LEAD) < dingtalk.index("[重点商机](")


def test_pick_report_sections_serializes_insight_body(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    sections = pick_report_sections([_row()], period_key="2026-10-13", slots=(_slot(),))
    assert sections == [
        {
            "key": "leader_efficiency_insight",
            "title": "团队人效",
            "item_type": PERSONA_TYPE,
            "url": "https://example.com/v2/department-highlights?date=2026-10-13&tab=leader_efficiency_insight",
            "items": [
                {
                    "unique_id": "insight-1",
                    "summary": "人均产出",
                    "detail_text": "详情",
                }
            ],
        }
    ]


def test_omits_report_when_body_is_blank(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    sections = pick_report_sections(
        [_row(summary="", detail_text="")],
        period_key="2026-10-13",
        slots=(_slot(),),
    )
    assert sections == []


def test_load_by_oauth_id_returns_persona(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )

    class _Result:
        def all(self):
            return [_row()]

    session = MagicMock()
    session.exec.return_value = _Result()
    entries = load_leader_insight_reports(session, OAUTH_ID, TUESDAY, slots=(_slot(),))
    assert entries == [
        {
            "key": "leader_efficiency_insight",
            "label": "团队人效",
            "url": "https://example.com/v2/department-highlights?date=2026-10-13&tab=leader_efficiency_insight",
        }
    ]


def test_monday_matches_execution_date(monkeypatch):
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )

    class _Miss:
        def all(self):
            return [_row(period_key="2026-10-13")]

    class _Hit:
        def all(self):
            return [_row(period_key="2026-10-12")]

    miss = MagicMock()
    miss.exec.return_value = _Miss()
    assert load_leader_insight_reports(miss, OAUTH_ID, MONDAY, slots=(_slot(),)) == []

    hit = MagicMock()
    hit.exec.return_value = _Hit()
    assert load_leader_insight_reports(hit, OAUTH_ID, MONDAY, slots=(_slot(),)) == [
        {
            "key": "leader_efficiency_insight",
            "label": "团队人效",
            "url": "https://example.com/v2/department-highlights?date=2026-10-12&tab=leader_efficiency_insight",
        }
    ]


def test_load_skips_when_oauth_id_missing():
    session = MagicMock()
    assert load_leader_insight_reports(session, "", TUESDAY, slots=(_slot(),)) == []
    session.exec.assert_not_called()


def test_load_returns_empty_when_query_fails():
    session = MagicMock()
    session.exec.side_effect = RuntimeError("db down")
    assert load_leader_insight_reports(session, OAUTH_ID, TUESDAY, slots=(_slot(),)) == []


def test_unconfigured_slots_do_not_query():
    session = MagicMock()
    by_key = {slot.key: slot for slot in LEADER_REPORT_SLOTS}
    assert by_key["key_deal_review"].insight_types == INSIGHT_KEY_OPPORTUNITIES
    assert by_key["leader_efficiency_insight"].insight_types == INSIGHT_PERSONA
    assert by_key["team_operating_trend"].insight_types == INSIGHT_BUSINESS_TREND
    assert all(slot.entity_type == ENTITY_USER for slot in LEADER_REPORT_SLOTS)
    assert load_leader_insight_reports(session, OAUTH_ID, TUESDAY, slots=(_slot(insight_types=()),)) == []
    assert load_leader_insight_sections(session, OAUTH_ID, TUESDAY, slots=(_slot(insight_types=()),)) == []
    session.exec.assert_not_called()
