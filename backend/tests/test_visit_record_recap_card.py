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
        "app.services.visit_record_recap_card.build_visit_record_page_url",
        lambda record_id: f"https://app.example/v2/behavior/{record_id}",
    )
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
    visit_detail = "[查看行为详情](https://app.example/v2/behavior/rec-1)"
    report = "[完整复盘报告](https://app.example/v2/behavior/rec-1?panel=recap)"
    assert card.title == "正常 · 9月20日 · 星辰科技"
    assert card.header_template == "green"
    assert (
        "跟进人：李华\n创建时间：9月20日 19:05\n商机：年度续约\n" + visit_detail
    ) in card.feishu_body
    assert visit_detail in card.feishu_body
    assert "panel=recap" not in visit_detail
    assert "客户确认下季度扩容" in card.feishu_body
    assert report in card.feishu_body
    assert card.feishu_body.index(visit_detail) < card.feishu_body.index("客户确认下季度扩容")
    assert card.feishu_body.index("客户确认下季度扩容") < card.feishu_body.index("完整复盘报告")
    assert "接下来可以看看这些：" not in card.feishu_body
    assert card.dingtalk_text.startswith("### 正常 · 9月20日 · 星辰科技")
    assert DINGTALK_PARAGRAPH_GAP in card.dingtalk_text
    assert (
        "跟进人：李华\n创建时间：9月20日 19:05\n商机：年度续约\n" + visit_detail
    ) in card.dingtalk_text


def test_blank_opportunity_is_omitted_from_lite_card(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_page_url",
        lambda record_id: f"https://app.example/v2/behavior/{record_id}",
    )
    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_recap_page_url",
        lambda record_id, query="panel=recap": f"https://app.example/v2/behavior/{record_id}?{query}",
    )
    record = {
        "visit_communication_date": "2026-09-24",
        "last_modified_time": "2026-09-24 15:17:00",
        "followup_object_name": "易慧生物技术（杭州）有限公司",
        "followup_record": "本次拜访确认客户微服务链路追踪存在延迟。",
        "recorder": "高娜",
    }
    visit_detail = "[查看行为详情](https://app.example/v2/behavior/rec-1)"
    for opportunity_name in (None, "", "  ", "--"):
        card = build_recap_lite_card(
            "rec-1",
            {**record, "opportunity_name": opportunity_name},
            recorder_name="高娜",
            insight={"severity": "LOW", "summary": "本次拜访确认客户微服务链路追踪存在延迟。"},
        )
        assert "商机" not in card.feishu_body
        assert "商机" not in card.dingtalk_text
        assert f"跟进人：高娜\n创建时间：9月24日 15:17\n{visit_detail}" in card.feishu_body


def test_build_recap_lite_card_uses_insight_summary_not_followup_record(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_page_url",
        lambda record_id: f"https://app.example/v2/behavior/{record_id}",
    )
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


def test_sales_lite_card_appends_extract_links(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_page_url",
        lambda record_id: f"https://app.example/v2/behavior/{record_id}",
    )
    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_recap_page_url",
        lambda record_id, query="panel=recap": f"https://app.example/v2/behavior/{record_id}?{query}",
    )
    monkeypatch.setattr(
        "app.utils.push_page_urls.build_visit_record_extract_section_url",
        lambda record_id, section, recap_query="panel=recap": (
            f"https://app.example/v2/behavior/{record_id}?{recap_query}&extract={section}"
        ),
    )
    card = build_recap_lite_card(
        "rec-1",
        {
            "visit_communication_date": "2026-09-20",
            "last_modified_time": "2026-09-20 19:05:00",
            "followup_object_name": "星辰科技",
            "recorder": "李华",
        },
        recorder_name="李华",
        insight={"severity": "LOW", "summary": "客户确认下季度扩容。"},
        extract_links=[
            {
                "key": "follow_ups",
                "title": "待我跟进",
                "count": 2,
                "preview": "周五报价",
            },
            {
                "key": "key_issues",
                "title": "当前最关键问题",
                "count": 1,
            },
        ],
    )
    visit_detail = "[查看行为详情](https://app.example/v2/behavior/rec-1)"
    report = "[完整复盘报告](https://app.example/v2/behavior/rec-1?panel=recap)"
    follow_ups = "[待我跟进（2）](https://app.example/v2/behavior/rec-1?panel=recap&extract=follow_ups)"
    assert visit_detail in card.feishu_body
    assert report in card.feishu_body
    assert "接下来可以看看这些：" in card.feishu_body
    assert follow_ups in card.feishu_body
    assert "[当前最关键问题](https://app.example/v2/behavior/rec-1?panel=recap&extract=key_issues)" in card.feishu_body
    assert card.feishu_body.index(visit_detail) < card.feishu_body.index("客户确认下季度扩容")
    assert card.feishu_body.index("客户确认下季度扩容") < card.feishu_body.index(report)
    assert card.feishu_body.index(report) < card.feishu_body.index("接下来可以看看这些：")
    assert card.feishu_body.index("接下来可以看看这些：") < card.feishu_body.index(follow_ups)
    assert "extract=follow_ups" in card.dingtalk_text
    assert "接下来可以看看这些：" in card.dingtalk_text
    leader = build_recap_lite_card(
        "rec-1",
        {
            "visit_communication_date": "2026-09-20",
            "last_modified_time": "2026-09-20 19:05:00",
            "followup_object_name": "星辰科技",
            "recorder": "李华",
        },
        recorder_name="李华",
        insight={"severity": "HIGH", "summary": "上级视角摘要"},
    )
    assert "extract=" not in leader.feishu_body
    assert "待我跟进" not in leader.feishu_body
    assert "接下来可以看看这些：" not in leader.feishu_body
    assert "[完整复盘报告](https://app.example/v2/behavior/rec-1?panel=recap)" in leader.feishu_body
