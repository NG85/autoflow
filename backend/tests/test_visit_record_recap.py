"""拜访复盘查询：视角决定是否附带结构化抽取。"""

from unittest.mock import MagicMock

import pytest

from app.services.visit_record_extract_reader import DEFAULT_PROFILE_ID, empty_extract_response
from app.services.visit_record_insight_reader import (
    RECAP_VIEW_LEADER,
    RECAP_VIEW_SALES,
    VISIT_INSIGHT_SALES_TYPE,
    VisitRecordInsight,
)
from app.services.visit_record_recap import (
    load_visit_record_recap_response,
    resolve_viewer_recap,
)


def _insight(view: str, summary: str) -> VisitRecordInsight:
    insight_type = (
        VISIT_INSIGHT_SALES_TYPE if view == RECAP_VIEW_SALES else "POSTVISIT_REVIEW_LEADER_VIEW"
    )
    return VisitRecordInsight(
        unique_id=view,
        entity_id="rec-1",
        insight_type=insight_type,
        title="标题",
        summary=summary,
        detail_text="",
        category="复盘",
        severity="LOW",
        generated_at=None,
    )


def _extract() -> dict:
    return {
        "visit_id": "rec-1",
        "profile_id": DEFAULT_PROFILE_ID,
        "items": {"SUGGESTED_ACTION": [{"claim": "确认CFO参与方式"}]},
        "card_links": [{"key": "follow_ups", "title": "待我跟进", "count": 1}],
    }


def test_no_recap_skips_extract_and_reporting_chain(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.resolve_viewer_recap",
        lambda *args, **kwargs: None,
    )
    called = []
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_extract_response",
        lambda *args, **kwargs: called.append("extract") or _extract(),
    )

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder",
        viewer_user_id="viewer",
    )

    assert called == []
    assert data["view"] is None
    assert data["insight"] is None
    assert data["extract"] == empty_extract_response("rec-1")


def test_sales_view_includes_insight_and_extract(monkeypatch):
    sales = _insight(RECAP_VIEW_SALES, "客户确认下季度扩容。")
    monkeypatch.setattr(
        "app.services.visit_record_recap.resolve_viewer_recap",
        lambda *args, **kwargs: (RECAP_VIEW_SALES, sales),
    )
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_extract_response",
        lambda *args, **kwargs: _extract(),
    )

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder",
        viewer_user_id="recorder",
    )

    assert data["view"] == RECAP_VIEW_SALES
    assert data["insight"]["summary"] == "客户确认下季度扩容。"
    assert data["extract"]["card_links"][0]["key"] == "follow_ups"


def test_leader_view_omits_extract_items(monkeypatch):
    leader = _insight(RECAP_VIEW_LEADER, "上级：注意回款节奏")
    monkeypatch.setattr(
        "app.services.visit_record_recap.resolve_viewer_recap",
        lambda *args, **kwargs: (RECAP_VIEW_LEADER, leader),
    )
    called = []
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_extract_response",
        lambda *args, **kwargs: called.append("extract") or _extract(),
    )

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder",
        viewer_user_id="leader",
    )

    assert called == []
    assert data["view"] == RECAP_VIEW_LEADER
    assert data["insight"]["summary"] == "上级：注意回款节奏"
    assert data["extract"]["items"] == {}
    assert data["extract"]["card_links"] == []


def test_resolve_failure_returns_empty_recap(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("insight down")

    monkeypatch.setattr("app.services.visit_record_recap.resolve_viewer_recap", _boom)

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder",
        viewer_user_id="viewer",
    )

    assert data["view"] is None
    assert data["insight"] is None
    assert data["extract"]["items"] == {}


def test_recorder_with_insight_does_not_query_reporting_chain(monkeypatch):
    sales = _insight(RECAP_VIEW_SALES, "销售摘要")
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: {RECAP_VIEW_SALES: sales, RECAP_VIEW_LEADER: None},
    )
    oauth = MagicMock()
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)

    resolved = resolve_viewer_recap(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="recorder-1",
    )

    oauth.get_reporting_chain_leaders.assert_not_called()
    assert resolved is not None
    view, picked = resolved
    assert view == RECAP_VIEW_SALES
    assert picked is sales


def test_no_insight_rows_do_not_query_reporting_chain(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: {RECAP_VIEW_SALES: None, RECAP_VIEW_LEADER: None},
    )
    profiles = MagicMock()
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)

    resolved = resolve_viewer_recap(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="someone-else",
    )

    assert resolved is None
    profiles.get_by_user_id.assert_not_called()


@pytest.mark.parametrize(
    "visit_id",
    ["", "   "],
)
def test_blank_visit_id_is_empty(visit_id):
    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id=visit_id,
        recorder_id=None,
        viewer_user_id="viewer",
    )
    assert data["visit_id"] == ""
    assert data["view"] is None
