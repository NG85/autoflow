"""例会管理评价卡：摘要、完整报告链接、抽取按钮，发给 CXO 今日重点的同一批人。"""

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from app.api.routes.notification import _handle_leader_meeting_review_cxo_card_push
from app.api.routes.notification_schemas import (
    LeaderMeetingReviewCxoCardPushRequest,
    PushNotificationRequest,
)
from app.services.leader_meeting_review_card import (
    build_leader_meeting_wecom_template_card,
    feishu_card_parts,
)
from app.services.leader_meeting_review_cxo_card import (
    assemble_cxo_card,
    cxo_extracts_for_viewer,
    handle_leader_meeting_review_cxo_card,
    with_owner_name,
)


def _insight(
    unique_id="insight-new",
    *,
    summary="这场会抓住了关键问题。",
    detail="## 本场总评\n这场会抓住了关键问题。",
    generated_at=None,
    row_id=2,
):
    return SimpleNamespace(
        unique_id=unique_id,
        summary=summary,
        detail_text=detail,
        period_key="RPT-PARENT",
        generated_at=generated_at or datetime(2026, 11, 23, 10, 0),
        id=row_id,
        entity_type="USER",
        entity_id="leader-1",
        insight_type="LEADER_MEETING_BEHAVIOR_EVAL",
        is_deleted=0,
        expires_at=None,
    )


def _row(item_type, claim, *, confidence=0.8, unique_id="item-1"):
    return SimpleNamespace(
        unique_id=unique_id,
        item_type=item_type,
        subject_type="USER",
        subject_id="leader-1",
        status="PENDING",
        is_deleted=0,
        report_date=date(2026, 11, 23),
        payload={"claim": claim, "why": "原文", "confidence": confidence},
    )


def test_push_union_accepts_cxo_card():
    payload = TypeAdapter(PushNotificationRequest).validate_python(
        {
            "type": "leader_meeting_review_cxo_card",
            "insight_ids": [" insight-1 ", "insight-1"],
            "recipient_user_ids": ["cxo-1", ""],
            "report_date": "2026-11-23",
            "source_report_id": "RPT-1",
        }
    )
    assert isinstance(payload, LeaderMeetingReviewCxoCardPushRequest)
    assert payload.insight_ids == ["insight-1"]
    assert payload.recipient_user_ids == ["cxo-1"]


def test_cxo_card_requires_insight_and_allows_omitted_recipients():
    with pytest.raises(ValidationError):
        LeaderMeetingReviewCxoCardPushRequest(
            insight_ids=[" "],
            recipient_user_ids=["cxo-1"],
        )
    payload = LeaderMeetingReviewCxoCardPushRequest(insight_ids=["insight-1"])
    assert payload.recipient_user_ids is None


def test_card_body_is_summary_and_extracts_are_buttons(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.build_leader_meeting_cxo_report_url",
        lambda report_id: f"https://example.com/review/{report_id}?tab=management",
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.build_leader_meeting_cxo_section_url",
        lambda insight_id, section: f"https://example.com/cxo/{insight_id}/{section}",
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.build_leader_meeting_review_report_url",
        lambda report_id, report_date="": f"https://example.com/review/{report_id}",
    )
    assembled = assemble_cxo_card(
        _insight(),
        [
            _row("LEADER_BEHAVIOR_COACHING", "不应出现在正文", confidence=0.4, unique_id="c1"),
            _row("LEADER_BEHAVIOR_COACHING", "也不应出现在正文", confidence=0.9, unique_id="c2"),
        ],
        report_date="2026-11-23",
        source_report_id="RPT-1",
    )
    body, report, buttons = feishu_card_parts(assembled)
    assert body == "这场会抓住了关键问题。"
    assert "不应出现在正文" not in body
    assert report == (
        "[完整评价报告](https://example.com/review/RPT-1?tab=management)"
        " ｜ "
        "[原复盘报告](https://example.com/review/RPT-1)"
    )
    assert buttons == [
        ("管理方法建议（2）", "https://example.com/cxo/insight-new/leader_behavior_coaching")
    ]
    assert assembled["card_title"] == "例会管理评价｜2026-11-23"
    assert assembled["can_manage"] is False
    named = with_owner_name(assembled, "王磊")
    named_body, named_report, named_buttons = feishu_card_parts(named)
    assert named["card_title"] == "例会管理评价｜王磊｜2026-11-23"
    assert named_body == "负责人：王磊\n\n这场会抓住了关键问题。"
    assert named_report == report
    assert named_buttons == buttons
    wecom = build_leader_meeting_wecom_template_card(named, title=named["card_title"])
    assert [item["title"] for item in wecom["jump_list"]] == ["完整评价报告", "原复盘报告"]
    assert wecom["horizontal_content_list"] == [
        {
            "type": 1,
            "keyname": "方法建议",
            "value": "2条",
            "url": "https://example.com/cxo/insight-new/leader_behavior_coaching",
        }
    ]
    undated = assemble_cxo_card(_insight(), [], source_report_id="RPT-1")
    assert undated["report_date"] is None
    assert undated["card_title"] == "例会管理评价"
    assert undated["report_url"].endswith("/RPT-1?tab=management")


def test_wecom_labels_match_the_feishu_card(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.build_leader_meeting_cxo_report_url",
        lambda report_id: f"https://example.com/review/{report_id}?tab=management",
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.build_leader_meeting_cxo_section_url",
        lambda insight_id, section: f"https://example.com/cxo/{insight_id}/{section}",
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.build_leader_meeting_review_report_url",
        lambda report_id, report_date="": f"https://example.com/review/{report_id}",
    )
    one = assemble_cxo_card(
        _insight(),
        [_row("LEADER_BEHAVIOR_COACHING", "下一场先确认沟通人。")],
        source_report_id="RPT-1",
    )
    with_report = build_leader_meeting_wecom_template_card(one, title="例会管理评价")
    assert [item["title"] for item in with_report["jump_list"]] == [
        "完整评价报告",
        "原复盘报告",
    ]
    assert with_report["horizontal_content_list"][0]["keyname"] == "方法建议"
    assert with_report["horizontal_content_list"][0]["value"] == "查看"

    without_detail = assemble_cxo_card(
        _insight(detail=""),
        [_row("LEADER_BEHAVIOR_COACHING", "下一场先确认沟通人。")],
        source_report_id="RPT-1",
    )
    extract_only = build_leader_meeting_wecom_template_card(
        without_detail, title="例会管理评价"
    )
    assert [item["title"] for item in extract_only["jump_list"]] == [
        "管理方法建议",
        "原复盘报告",
    ]
    assert "horizontal_content_list" not in extract_only


def test_summary_only_still_sends_without_report_link(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.build_leader_meeting_cxo_report_url",
        lambda report_id: f"https://example.com/review/{report_id}?tab=management",
    )
    assembled = assemble_cxo_card(_insight(detail=""), [])
    body, report, buttons = feishu_card_parts(assembled)
    assert body == "这场会抓住了关键问题。"
    assert report == ""
    assert buttons == []


def _cxo(user_id, open_id):
    return {"user_id": user_id, "open_id": open_id, "platform": "feishu", "name": "CXO"}


def _enable_push(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.settings.LEADER_MEETING_CXO_CARD_PUSH_ENABLED",
        True,
    )


def test_push_uses_latest_insight_and_narrows_highlight_recipients(monkeypatch):
    _enable_push(monkeypatch)
    sent = []
    newer = _insight("insight-new", generated_at=datetime(2026, 11, 23, 12, 0), row_id=3)
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.load_latest_insight",
        lambda session, insight_ids: newer,
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.load_cxo_extract_rows",
        lambda session, insight_id: [
            _row("LEADER_BEHAVIOR_COACHING", "下一场先确认沟通人", unique_id="c1")
        ]
        if insight_id == "insight-new"
        else [],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.resolve_company_highlight_recipients",
        lambda session, recipient_user_ids=None: [_cxo("cxo-1", "ou_cxo")]
        if recipient_user_ids == ["cxo-1", "outsider"]
        else [_cxo("cxo-1", "ou_cxo"), _cxo("cxo-2", "ou_cxo_2")],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.deliver_card",
        lambda session, recipient, assembled: sent.append((recipient["open_id"], assembled["insight_id"]))
        or {"success": True},
    )
    result = handle_leader_meeting_review_cxo_card(
        None,
        insight_ids=["insight-old", "insight-new"],
        recipient_user_ids=["cxo-1", "outsider"],
        source_report_id="RPT-1",
    )
    assert result["pushed"] is True
    assert result["insight_id"] == "insight-new"
    assert result["reason"] is None
    assert result["recipient_user_ids"] == ["cxo-1"]
    assert sent == [("ou_cxo", "insight-new")]


def test_omitted_recipients_use_company_highlight_list(monkeypatch):
    _enable_push(monkeypatch)
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.load_latest_insight",
        lambda session, insight_ids: _insight("insight-new"),
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.load_cxo_extract_rows",
        lambda session, insight_id: [],
    )
    seen = []
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.resolve_company_highlight_recipients",
        lambda session, recipient_user_ids=None: seen.append(recipient_user_ids)
        or [_cxo("cxo-1", "ou_cxo")],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.deliver_card",
        lambda session, recipient, assembled: {"success": True},
    )
    result = handle_leader_meeting_review_cxo_card(None, insight_ids=["insight-new"])
    assert result["pushed"] is True
    assert seen == [None]
    assert result["recipient_user_ids"] == ["cxo-1"]


def test_disabled_push_skips_without_lookup(monkeypatch):
    from app.core.config import Settings

    assert Settings.model_fields["LEADER_MEETING_CXO_CARD_PUSH_ENABLED"].default is False
    called = []
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.settings.LEADER_MEETING_CXO_CARD_PUSH_ENABLED",
        False,
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.resolve_company_highlight_recipients",
        lambda *args, **kwargs: called.append("recipients"),
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.load_latest_insight",
        lambda *args, **kwargs: called.append("insight"),
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.deliver_card",
        lambda *args, **kwargs: called.append("send"),
    )
    result = handle_leader_meeting_review_cxo_card(
        None,
        insight_ids=["insight-1"],
        source_report_id="RPT-1",
    )
    assert called == []
    assert result["success"] is True
    assert result["pushed"] is False
    assert result["reason"] == "feature_disabled"
    assert result["insight_id"] == "insight-1"
    assert result["source_report_id"] == "RPT-1"


def test_missing_insight_does_not_send(monkeypatch):
    _enable_push(monkeypatch)
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.resolve_company_highlight_recipients",
        lambda session, recipient_user_ids=None: [_cxo("cxo-1", "ou_cxo")],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.load_latest_insight",
        lambda session, insight_ids: None,
    )
    called = []
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.deliver_card",
        lambda *args, **kwargs: called.append(args),
    )
    result = _handle_leader_meeting_review_cxo_card_push(
        None,
        LeaderMeetingReviewCxoCardPushRequest(
            insight_ids=["insight-1"],
            recipient_user_ids=["cxo-1"],
        ),
    )
    assert result["pushed"] is False
    assert result["reason"] == "insight_not_found"
    assert called == []


def test_extract_query_returns_items_without_the_report(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.load_cxo_extract_rows",
        lambda session, insight_id: [
            _row("LEADER_BEHAVIOR_COACHING", "下一场先确认沟通人。", unique_id="item-1")
        ]
        if insight_id == "insight-1"
        else [],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.load_threads",
        lambda session, subject_type, subject_ids: {},
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.viewer_is_company_highlight_recipient",
        lambda session, user_id, oauth_id=None, open_ids=(): user_id == "cxo-1",
    )
    own = cxo_extracts_for_viewer(None, "insight-1", "leader-1", viewer_user_id="cxo-1")
    assert own["can_manage"] is True
    assert own["items"]["leader_behavior_coaching"][0]["claim"] == "下一场先确认沟通人。"
    denied = cxo_extracts_for_viewer(None, "insight-1", "leader-1", viewer_user_id="leader-1")
    assert denied is None
    data = cxo_extracts_for_viewer(None, "insight-1", "cxo-oauth", viewer_user_id="cxo-1")
    assert data["insight_id"] == "insight-1"
    assert data["report_date"] == "2026-11-23"
    assert data["can_manage"] is True
    assert data["uploader_user_id"] is None
    assert "detail_text" not in data
    item = data["items"]["leader_behavior_coaching"][0]
    assert item["claim"] == "下一场先确认沟通人。"
    assert item["feedback"] is None
    assert item["tasks"] == []
    assert item["comments"] == []
    assert data["card_links"][0]["key"] == "leader_behavior_coaching"

    empty = cxo_extracts_for_viewer(None, "missing", "cxo-oauth", viewer_user_id="cxo-1")
    assert empty["items"] == {}
    assert empty["card_links"] == []
    assert empty["can_manage"] is False


def test_cxo_extract_route_returns_404_for_non_cxo(monkeypatch):
    from app.api.routes.crm.leader_meeting_review.router import get_leader_meeting_cxo_extracts

    monkeypatch.setattr(
        "app.api.routes.crm.leader_meeting_review.router._viewer_identity",
        lambda session, user: ("leader-1", []),
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_cxo_card.cxo_extracts_for_viewer",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(HTTPException) as caught:
        get_leader_meeting_cxo_extracts("insight-1", None, SimpleNamespace(id="leader-1"))
    assert caught.value.status_code == 404
    assert caught.value.detail == "管理评价抽取不存在或无权限"
