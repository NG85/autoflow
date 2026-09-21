"""轻量复盘卡文案与颜色。"""

from datetime import date, datetime

from app.services.visit_record_recap_card import (
    DINGTALK_PARAGRAPH_GAP,
    build_recap_lite_card,
    compose_recap_title,
    format_entry_time,
    format_visit_date_short,
    resolve_recap_quality,
)
from app.services.visit_record_insight_reader import VisitRecordInsight


def test_quality_from_insight_severity_only():
    assert resolve_recap_quality(insight={"severity": "LOW"}) == ("正常", "green")
    assert resolve_recap_quality(insight={"severity": "medium"}) == ("留意", "blue")
    assert resolve_recap_quality(insight={"severity": "HIGH"}) == ("需关注", "orange")
    assert resolve_recap_quality(None) == ("", "blue")
    assert resolve_recap_quality(insight={"severity": "unknown"}) == ("", "blue")


def test_date_and_entry_time_formats():
    assert format_visit_date_short(date(2026, 9, 20)) == "9月20日"
    assert format_entry_time("2026-09-20 19:05:33") == "9月20日 19:05"
    assert format_entry_time(datetime(2026, 9, 20, 19, 5)) == "9月20日 19:05"


def test_title_keeps_quality_date_account_and_truncates():
    title = compose_recap_title(
        "正常",
        "9月20日",
        "这是一个非常非常长的客户名称用来验证飞书卡片标题五十个字限制时必须截断处理吧",
    )
    assert title.startswith("正常 · 9月20日 · ")
    assert len(title) <= 50
    assert title.endswith("…")


def test_build_recap_lite_card_feishu_and_dingtalk(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_recap_page_url",
        lambda record_id, query="panel=recap": f"https://app.example/v2/behavior/{record_id}?{query}",
    )
    card = build_recap_lite_card(
        "rec-1",
        {
            "assessment_flag": "green",
            "visit_communication_date": "2026-09-20",
            "last_modified_time": "2026-09-20 19:05:00",
            "followup_object_name": "星辰科技",
            "opportunity_name": "年度续约",
            "followup_record": "客户确认下季度扩容，下周出方案。",
            "recorder": "李华",
        },
        recorder_name="李华",
        insight={"severity": "LOW", "summary": "客户确认下季度扩容，下周出方案。"},
    )
    assert card.title == "正常 · 9月20日 · 星辰科技"
    assert card.header_template == "green"
    assert "记录人：李华 · 跟进时间：9月20日 19:05 · 商机：年度续约" in card.feishu_body
    assert "客户确认下季度扩容" in card.feishu_body
    assert "[查看详情](https://app.example/v2/behavior/rec-1?panel=recap)" in card.feishu_body
    assert card.dingtalk_text.startswith("### 正常 · 9月20日 · 星辰科技")
    assert DINGTALK_PARAGRAPH_GAP in card.dingtalk_text
    assert "记录人：李华" in card.dingtalk_text
    assert "跟进时间：9月20日 19:05" in card.dingtalk_text
    assert "商机：年度续约" in card.dingtalk_text
    # 钉钉窄屏：字段分行，不挤在同一行
    assert "跟进时间：9月20日 19:05 · 商机" not in card.dingtalk_text


def test_build_recap_lite_card_uses_insight_summary_not_followup_record(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_recap_page_url",
        lambda record_id, query="panel=recap": f"https://app.example/v2/behavior/{record_id}?{query}",
    )
    insight = VisitRecordInsight(
        unique_id="ins-1",
        entity_id="rec-1",
        insight_type="POSTVISIT_REVIEW_SALES_VIEW",
        title="复盘标题",
        summary="客户确认下季度扩容，下周出方案。",
        detail_text="更长的完整复盘不应出现在轻量卡。",
        category="复盘",
        severity="HIGH",
        generated_at=None,
    )
    card = build_recap_lite_card(
        "rec-1",
        {
            "assessment_flag": "green",
            "visit_communication_date": "2026-09-20",
            "last_modified_time": "2026-09-20 19:05:00",
            "followup_object_name": "星辰科技",
            "followup_record": "这是销售自己填的跟进原文，不应作为轻量卡正文。",
            "recorder": "李华",
        },
        recorder_name="李华",
        insight=insight,
    )
    assert "客户确认下季度扩容，下周出方案。" in card.feishu_body
    assert "销售自己填的跟进原文" not in card.feishu_body
    assert "更长的完整复盘" not in card.feishu_body
    assert card.title.startswith("需关注 · ")
    assert card.header_template == "orange"
