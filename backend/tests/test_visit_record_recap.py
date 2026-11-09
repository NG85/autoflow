"""拜访复盘查询：视角决定是否附带结构化抽取。"""

from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.services.visit_record_extract_reader import DEFAULT_PROFILE_ID, empty_extract_response
from app.services.visit_record_insight_reader import (
    PERFORMANCE_VIEW_LEADER,
    PERFORMANCE_VIEW_SALES,
    RECAP_TAB_BUSINESS,
    RECAP_TAB_PERFORMANCE,
    RECAP_VIEW_LEADER,
    RECAP_VIEW_SALES,
    VISIT_INSIGHT_PERFORMANCE_LEADER_TYPE,
    VISIT_INSIGHT_PERFORMANCE_SALES_TYPE,
    VISIT_INSIGHT_SALES_TYPE,
    VisitRecordInsight,
)
from app.services.visit_record_recap import (
    RecapViewContext,
    load_visit_record_recap_response,
    resolve_viewer_recap,
)


def _insight(view: str, summary: str, *, detail_text: str = "") -> VisitRecordInsight:
    if view == RECAP_VIEW_SALES:
        insight_type = VISIT_INSIGHT_SALES_TYPE
    elif view == RECAP_VIEW_LEADER:
        insight_type = "POSTVISIT_REVIEW_LEAD_VIEW"
    elif view == PERFORMANCE_VIEW_SALES:
        insight_type = VISIT_INSIGHT_PERFORMANCE_SALES_TYPE
    else:
        insight_type = VISIT_INSIGHT_PERFORMANCE_LEADER_TYPE
    return VisitRecordInsight(
        unique_id=view,
        entity_id="rec-1",
        insight_type=insight_type,
        title="标题",
        summary=summary,
        detail_text=detail_text,
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
    assert data["performance"] is None
    assert data["tabs"] == []
    assert data["extract"] == empty_extract_response("rec-1")


def test_recorder_includes_insight_and_extract(monkeypatch):
    sales = _insight(RECAP_VIEW_SALES, "客户确认下季度扩容。")
    monkeypatch.setattr(
        "app.services.visit_record_recap.resolve_viewer_recap",
        lambda *args, **kwargs: (RECAP_VIEW_SALES, sales, None),
    )
    seen = {}

    def _load_extract(*args, **kwargs):
        seen["viewer_user_id"] = kwargs.get("viewer_user_id")
        return _extract()

    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_extract_response",
        _load_extract,
    )

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder",
        viewer_user_id="recorder",
    )

    assert seen["viewer_user_id"] == "recorder"
    assert data["view"] == RECAP_VIEW_SALES
    assert data["insight"]["summary"] == "客户确认下季度扩容。"
    assert data["performance"] is None
    assert data["tabs"] == [
        {"key": RECAP_TAB_BUSINESS, "title": "业务推进"},
        {"key": RECAP_TAB_PERFORMANCE, "title": "销售表现"},
    ]
    assert data["extract"]["card_links"][0]["key"] == "follow_ups"


def test_collaborator_sales_view_omits_extract(monkeypatch):
    """协同人可走 sales 复盘视角，但不附带抽取。"""
    sales = _insight(RECAP_VIEW_SALES, "协同人看销售视角摘要")
    monkeypatch.setattr(
        "app.services.visit_record_recap.resolve_viewer_recap",
        lambda *args, **kwargs: (RECAP_VIEW_SALES, sales, None),
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
        viewer_user_id="collaborator",
    )

    assert called == []
    assert data["view"] == RECAP_VIEW_SALES
    assert data["insight"]["summary"] == "协同人看销售视角摘要"
    assert data["extract"]["items"] == {}
    assert data["extract"]["card_links"] == []


def test_leader_view_omits_extract_items(monkeypatch):
    leader = _insight(RECAP_VIEW_LEADER, "上级：注意回款节奏")
    monkeypatch.setattr(
        "app.services.visit_record_recap.resolve_viewer_recap",
        lambda *args, **kwargs: (RECAP_VIEW_LEADER, leader, None),
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
    assert data["performance"] is None
    assert data["tabs"] == []
    assert data["extract"]["items"] == {}


def _both_insights():
    return {
        RECAP_VIEW_SALES: _insight(RECAP_VIEW_SALES, "销售摘要"),
        RECAP_VIEW_LEADER: _insight(RECAP_VIEW_LEADER, "上级摘要"),
    }


def test_recorder_with_insight_does_not_query_reporting_chain(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _both_insights(),
    )
    oauth = MagicMock()
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)
    profiles = MagicMock()
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)

    resolved = resolve_viewer_recap(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="recorder-1",
    )

    oauth.get_reporting_chain_leaders.assert_not_called()
    profiles.get_by_user_id.assert_not_called()
    assert resolved is not None
    view, picked, performance = resolved
    assert view == RECAP_VIEW_SALES
    assert picked.summary == "销售摘要"
    assert performance is None


def test_other_viewer_gets_no_recap(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _both_insights(),
    )
    viewer = MagicMock(oauth_user_id="ask_admin", oauth_users=[MagicMock(open_id="ou_admin")])
    recorder = MagicMock(user_id="recorder-user", direct_manager_id="ask_leader", oauth_users=[])
    profiles = MagicMock()
    profiles.get_by_user_id.return_value = viewer
    profiles.get_by_recorder_id.return_value = recorder
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)
    oauth = MagicMock()
    oauth.get_reporting_chain_leaders.return_value = [{"open_id": "ou_leader"}]
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)

    resolved = resolve_viewer_recap(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="admin-1",
    )

    oauth.get_reporting_chain_leaders.assert_called_once()
    assert resolved is None


def test_reporting_chain_leader_uses_leader_view(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _both_insights(),
    )
    viewer = MagicMock(oauth_user_id="ask_viewer", oauth_users=[MagicMock(open_id="ou_leader")])
    recorder = MagicMock(user_id="recorder-user", direct_manager_id=None, oauth_users=[])
    profiles = MagicMock()
    profiles.get_by_user_id.return_value = viewer
    profiles.get_by_recorder_id.return_value = recorder
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)
    oauth = MagicMock()
    oauth.get_reporting_chain_leaders.return_value = [{"open_id": "ou_leader"}]
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)

    resolved = resolve_viewer_recap(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="leader-1",
    )

    oauth.get_reporting_chain_leaders.assert_called_once()
    assert resolved is not None
    view, picked, performance = resolved
    assert view == RECAP_VIEW_LEADER
    assert picked.summary == "上级摘要"
    assert performance is None


def test_collaborator_gets_no_recap(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _both_insights(),
    )
    viewer = MagicMock(oauth_user_id="ask_collab", oauth_users=[MagicMock(open_id="ou_collab")])
    recorder = MagicMock(user_id="recorder-user", direct_manager_id="ask_leader", oauth_users=[])
    profiles = MagicMock()
    profiles.get_by_user_id.return_value = viewer
    profiles.get_by_recorder_id.return_value = recorder
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)
    oauth = MagicMock()
    oauth.get_reporting_chain_leaders.return_value = [{"open_id": "ou_leader"}]
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)

    resolved = resolve_viewer_recap(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="collab-1",
    )

    assert resolved is None


def test_sys_admin_sees_leader_insight_without_extract(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _both_insights(),
    )
    oauth = MagicMock()
    oauth.check_user_has_role.return_value = True
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)
    admin_id = UUID("33333333-3333-3333-3333-333333333333")

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id=admin_id,
    )

    oauth.get_reporting_chain_leaders.assert_not_called()
    oauth.check_user_has_role.assert_called_once()
    assert data["view"] == RECAP_VIEW_LEADER
    assert data["insight"]["summary"] == "上级摘要"
    assert data["extract"]["items"] == {}


def test_sys_admin_role_check_failure_does_not_open_recap(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _both_insights(),
    )
    viewer = MagicMock(oauth_user_id="ask_admin", oauth_users=[MagicMock(open_id="ou_admin")])
    recorder = MagicMock(user_id="recorder-user", direct_manager_id="ask_leader", oauth_users=[])
    profiles = MagicMock()
    profiles.get_by_user_id.return_value = viewer
    profiles.get_by_recorder_id.return_value = recorder
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)
    oauth = MagicMock()
    oauth.check_user_has_role.side_effect = RuntimeError("oauth down")
    oauth.get_reporting_chain_leaders.return_value = [{"open_id": "ou_leader"}]
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)
    admin_id = UUID("44444444-4444-4444-4444-444444444444")

    resolved = resolve_viewer_recap(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id=admin_id,
    )

    assert resolved is None


def test_reporting_leader_lookup_is_cached_per_recorder(monkeypatch):
    viewer = MagicMock(oauth_user_id="ask_viewer", oauth_users=[MagicMock(open_id="ou_leader")])
    recorder = MagicMock(user_id="recorder-user", direct_manager_id=None, oauth_users=[])
    profiles = MagicMock()
    profiles.get_by_user_id.return_value = viewer
    profiles.get_by_recorder_id.return_value = recorder
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)
    oauth = MagicMock()
    oauth.get_reporting_chain_leaders.return_value = [{"open_id": "ou_leader"}]
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)

    context = RecapViewContext(MagicMock(), "leader-1")
    assert context.is_reporting_leader("recorder-1") is True
    assert context.is_reporting_leader("recorder-1") is True
    oauth.get_reporting_chain_leaders.assert_called_once()
    profiles.get_by_user_id.assert_called_once()


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
    assert data["performance"] is None
    assert data["tabs"] == []


def _page_insights(**overrides):
    insights = {
        RECAP_VIEW_SALES: None,
        RECAP_VIEW_LEADER: None,
        PERFORMANCE_VIEW_SALES: None,
        PERFORMANCE_VIEW_LEADER: None,
    }
    insights.update(overrides)
    return insights


def test_sales_tabs_keep_only_the_sales_performance(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _page_insights(
            **{
                RECAP_VIEW_SALES: _insight(RECAP_VIEW_SALES, "销售复盘", detail_text="复盘正文"),
                RECAP_VIEW_LEADER: _insight(RECAP_VIEW_LEADER, "上级复盘"),
                PERFORMANCE_VIEW_SALES: _insight(
                    PERFORMANCE_VIEW_SALES,
                    "销售本场问清了预算。",
                    detail_text="表现报告",
                ),
                PERFORMANCE_VIEW_LEADER: _insight(PERFORMANCE_VIEW_LEADER, "没有约到决策人。"),
            }
        ),
    )

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="recorder-1",
    )

    assert data["view"] == RECAP_VIEW_SALES
    assert data["tabs"] == [
        {"key": RECAP_TAB_BUSINESS, "title": "业务推进"},
        {"key": RECAP_TAB_PERFORMANCE, "title": "销售表现"},
    ]
    assert data["insight"]["summary"] == "销售复盘"
    assert data["insight"]["detail_text"] == "复盘正文"
    assert data["performance"]["summary"] == "销售本场问清了预算。"
    assert data["performance"]["detail_text"] == "表现报告"
    assert data["performance"]["insight_type"] == VISIT_INSIGHT_PERFORMANCE_SALES_TYPE
    assert "上级复盘" not in str(data)
    assert "没有约到决策人" not in str(data)


def test_performance_only_still_resolves_view_and_loads_extract(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _page_insights(
            **{PERFORMANCE_VIEW_SALES: _insight(PERFORMANCE_VIEW_SALES, "下一步没有负责人。")}
        ),
    )
    oauth = MagicMock()
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_extract_response",
        lambda *args, **kwargs: _extract(),
    )

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="recorder-1",
    )

    oauth.get_reporting_chain_leaders.assert_not_called()
    assert data["view"] == RECAP_VIEW_SALES
    assert data["insight"] is None
    assert data["performance"]["summary"] == "下一步没有负责人。"
    assert data["tabs"] == [
        {"key": RECAP_TAB_BUSINESS, "title": "业务推进"},
        {"key": RECAP_TAB_PERFORMANCE, "title": "销售表现"},
    ]
    assert data["extract"]["card_links"][0]["key"] == "follow_ups"


def test_performance_only_for_other_viewer_queries_chain_and_stays_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.visit_record_recap.load_visit_record_insights_by_view",
        lambda *args, **kwargs: _page_insights(
            **{PERFORMANCE_VIEW_LEADER: _insight(PERFORMANCE_VIEW_LEADER, "上级评价")}
        ),
    )
    viewer = MagicMock(oauth_user_id="ask_collab", oauth_users=[MagicMock(open_id="ou_collab")])
    recorder = MagicMock(user_id="recorder-user", direct_manager_id="ask_leader", oauth_users=[])
    profiles = MagicMock()
    profiles.get_by_user_id.return_value = viewer
    profiles.get_by_recorder_id.return_value = recorder
    monkeypatch.setattr("app.services.visit_record_recap.UserProfileRepo", lambda: profiles)
    oauth = MagicMock()
    oauth.check_user_has_role.return_value = False
    oauth.get_reporting_chain_leaders.return_value = [{"open_id": "ou_leader"}]
    monkeypatch.setattr("app.services.oauth_service.oauth_client", oauth)

    data = load_visit_record_recap_response(
        MagicMock(),
        visit_record_id="rec-1",
        recorder_id="recorder-1",
        viewer_user_id="collab-1",
    )

    oauth.get_reporting_chain_leaders.assert_called_once()
    assert data["view"] is None
    assert data["performance"] is None
    assert data["tabs"] == []
    assert "上级评价" not in str(data)
