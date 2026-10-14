"""只读 crm_entity_insight：拜访 VISIT 销售/上级两条复盘。"""

from datetime import datetime
from unittest.mock import MagicMock

from app.services.visit_record_insight_reader import (
    RECAP_VIEW_LEADER,
    RECAP_VIEW_SALES,
    VISIT_INSIGHT_LEADER_TYPE,
    VISIT_INSIGHT_SALES_TYPE,
    VisitRecordInsight,
    has_visit_recap_insight,
    load_visit_recap_types_by_record_id,
    load_visit_record_insight,
    load_visit_record_insights_by_view,
    pick_visit_record_insight,
    recap_view_for_role,
    recap_view_for_viewer,
    resolve_visit_record_detail_recap,
    viewer_has_visible_recap,
    viewer_is_leader_of_recorder,
    viewer_is_recorder,
)


def _row(**overrides):
    base = {
        "unique_id": "ins-1",
        "entity_id": "rec-1",
        "insight_type": VISIT_INSIGHT_SALES_TYPE,
        "title": "标题",
        "summary": "客户确认下季度扩容。",
        "detail_text": "完整复盘……",
        "category": "复盘",
        "severity": "LOW",
        "generated_at": datetime(2026, 9, 20, 11, 0, 0),
    }
    base.update(overrides)
    return base


def test_recap_view_for_viewer_only_recorder_and_reporting_leader():
    assert recap_view_for_viewer() is None
    assert recap_view_for_viewer(is_recorder=True) == RECAP_VIEW_SALES
    assert recap_view_for_viewer(is_leader=True) == RECAP_VIEW_LEADER
    assert recap_view_for_viewer(is_recorder=True, is_leader=True) == RECAP_VIEW_SALES


def test_recap_view_for_viewer_recorder_id_ignores_uuid_hyphens():
    hyphen = "11111111-1111-1111-1111-111111111111"
    compact = "11111111111111111111111111111111"
    assert viewer_is_recorder(hyphen, compact) is True


def test_viewer_is_leader_of_recorder_by_direct_manager_or_reporting_chain():
    viewer_id = "22222222-2222-2222-2222-222222222222"
    assert viewer_is_leader_of_recorder(
        viewer_user_id=viewer_id,
        viewer_oauth_user_id="ask_leader",
        recorder_direct_manager_id="ask_leader",
    ) is True
    assert viewer_is_leader_of_recorder(
        viewer_user_id=viewer_id,
        recorder_direct_manager_id=viewer_id,
    ) is True
    assert viewer_is_leader_of_recorder(
        viewer_user_id=viewer_id,
        viewer_open_ids=["ou_leader"],
        reporting_chain_leader_open_ids=["ou_leader"],
    ) is True
    assert viewer_is_leader_of_recorder(
        viewer_user_id=viewer_id,
        viewer_oauth_user_id="ask_other_dept_leader",
        viewer_open_ids=["ou_other"],
        recorder_direct_manager_id="ask_leader",
        reporting_chain_leader_open_ids=["ou_leader"],
    ) is False


def test_direct_manager_not_used_when_reporting_chain_has_other_leader():
    viewer_id = "22222222-2222-2222-2222-222222222222"
    assert viewer_is_leader_of_recorder(
        viewer_user_id=viewer_id,
        viewer_oauth_user_id="ask_direct",
        viewer_open_ids=["ou_direct"],
        recorder_direct_manager_id="ask_direct",
        reporting_chain_leader_open_ids=["ou_dept_leader"],
    ) is False


def test_other_viewer_has_no_recap_view():
    assert recap_view_for_viewer(is_recorder=False, is_leader=False) is None


def test_sys_admin_sees_leader_view_unless_they_are_the_recorder():
    assert recap_view_for_viewer(is_sys_admin=True) == RECAP_VIEW_LEADER
    assert recap_view_for_viewer(is_recorder=True, is_sys_admin=True) == RECAP_VIEW_SALES


def test_recap_view_for_role():
    assert recap_view_for_role("recorder") == RECAP_VIEW_SALES
    assert recap_view_for_role("collaborative_participant") == RECAP_VIEW_SALES
    assert recap_view_for_role("leader") == RECAP_VIEW_LEADER
    assert recap_view_for_role("configured_cc") == RECAP_VIEW_LEADER
    assert recap_view_for_role("leader", is_group=True) == RECAP_VIEW_LEADER
    assert recap_view_for_role("recorder", is_group=True) == RECAP_VIEW_LEADER


def test_pick_sales_view_does_not_take_newer_leader_row():
    rows = [
        _row(
            unique_id="leader",
            insight_type=VISIT_INSIGHT_LEADER_TYPE,
            summary="上级视角摘要",
            generated_at=datetime(2026, 9, 20, 12, 0, 0),
        ),
        _row(
            unique_id="sales",
            insight_type=VISIT_INSIGHT_SALES_TYPE,
            summary="客户确认下季度扩容。",
            generated_at=datetime(2026, 9, 20, 11, 0, 0),
        ),
    ]
    picked = pick_visit_record_insight(rows, preferred_types=[VISIT_INSIGHT_SALES_TYPE])
    assert picked is not None
    assert picked.unique_id == "sales"
    assert picked.insight_type == VISIT_INSIGHT_SALES_TYPE


def test_pick_returns_none_when_preferred_type_missing():
    rows = [
        _row(insight_type="RISK", summary="有回款风险"),
        _row(insight_type=VISIT_INSIGHT_LEADER_TYPE, summary="上级视角"),
    ]
    picked = pick_visit_record_insight(rows, preferred_types=[VISIT_INSIGHT_SALES_TYPE])
    assert picked is None


def test_recap_text_prefers_summary_then_title():
    insight = VisitRecordInsight(
        unique_id="ins-1",
        entity_id="rec-1",
        insight_type=VISIT_INSIGHT_SALES_TYPE,
        title="标题",
        summary="",
        detail_text="长文",
        category="",
        severity="LOW",
        generated_at=None,
    )
    assert insight.recap_text() == "标题"


def test_load_visit_record_insights_by_view_splits_sales_and_leader():
    rows = [
        _row(
            unique_id="leader",
            insight_type=VISIT_INSIGHT_LEADER_TYPE,
            summary="上级：注意回款节奏",
            severity="HIGH",
        ),
        _row(
            unique_id="sales",
            insight_type=VISIT_INSIGHT_SALES_TYPE,
            summary="客户确认下季度扩容。",
            severity="LOW",
        ),
    ]
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.exec.return_value = result

    insights = load_visit_record_insights_by_view(session, "rec-1")
    assert insights[RECAP_VIEW_SALES].unique_id == "sales"
    assert insights[RECAP_VIEW_LEADER].unique_id == "leader"
    assert insights[RECAP_VIEW_SALES].summary == "客户确认下季度扩容。"
    assert insights[RECAP_VIEW_LEADER].summary == "上级：注意回款节奏"

    statement = session.exec.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "crm_entity_insight" in sql
    assert "VISIT" in sql
    assert "rec-1" in sql


def test_load_visit_record_insight_defaults_to_sales_view():
    rows = [
        _row(insight_type=VISIT_INSIGHT_LEADER_TYPE, summary="上级视角"),
        _row(insight_type=VISIT_INSIGHT_SALES_TYPE, summary="销售视角"),
    ]
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.exec.return_value = result
    insight = load_visit_record_insight(session, "rec-1")
    assert insight is not None
    assert insight.summary == "销售视角"


def test_load_visit_record_insight_returns_none_on_error():
    session = MagicMock()
    session.exec.side_effect = RuntimeError("table missing")
    assert load_visit_record_insight(session, "rec-1") is None
    assert load_visit_record_insight(None, "rec-1") is None
    assert load_visit_record_insight(session, "") is None
    assert load_visit_record_insights_by_view(None, "rec-1") == {
        RECAP_VIEW_SALES: None,
        RECAP_VIEW_LEADER: None,
    }


def test_get_collaborative_participants_raw_reads_unformatted_json():
    from app.repositories.visit_record import VisitRecordRepo

    session = MagicMock()
    raw = '[{"name":"王芳","ask_id":"ask_collab"}]'
    session.exec.return_value.first.return_value = raw
    assert VisitRecordRepo().get_collaborative_participants_raw(session, "rec-1") == raw
    assert VisitRecordRepo().get_collaborative_participants_raw(session, "  ") is None
    assert VisitRecordRepo().get_collaborative_participants_raw(session, "") is None


def test_no_insight_skips_view_resolver():
    called = []
    result = resolve_visit_record_detail_recap(
        {RECAP_VIEW_SALES: None, RECAP_VIEW_LEADER: None},
        resolve_view=lambda: called.append("oauth") or RECAP_VIEW_LEADER,
    )
    assert result is None
    assert called == []
    assert has_visit_recap_insight({RECAP_VIEW_SALES: None, RECAP_VIEW_LEADER: None}) is False


def test_has_insight_uses_given_view():
    sales = VisitRecordInsight(
        unique_id="ins-1",
        entity_id="rec-1",
        insight_type=VISIT_INSIGHT_SALES_TYPE,
        title="标题",
        summary="销售摘要",
        detail_text="",
        category="复盘",
        severity="LOW",
        generated_at=None,
    )
    result = resolve_visit_record_detail_recap(
        {RECAP_VIEW_SALES: sales, RECAP_VIEW_LEADER: None},
        view=RECAP_VIEW_SALES,
    )
    assert result is not None
    view, picked = result
    assert view == RECAP_VIEW_SALES
    assert picked is sales


def test_has_insight_uses_view_resolver():
    called = []
    sales = VisitRecordInsight(
        unique_id="sales",
        entity_id="rec-1",
        insight_type=VISIT_INSIGHT_SALES_TYPE,
        title="标题",
        summary="销售摘要",
        detail_text="",
        category="复盘",
        severity="LOW",
        generated_at=None,
    )
    leader = VisitRecordInsight(
        unique_id="leader",
        entity_id="rec-1",
        insight_type=VISIT_INSIGHT_LEADER_TYPE,
        title="标题",
        summary="上级摘要",
        detail_text="",
        category="复盘",
        severity="HIGH",
        generated_at=None,
    )
    result = resolve_visit_record_detail_recap(
        {RECAP_VIEW_SALES: sales, RECAP_VIEW_LEADER: leader},
        resolve_view=lambda: called.append("oauth") or RECAP_VIEW_LEADER,
    )
    assert called == ["oauth"]
    assert result is not None
    view, picked = result
    assert view == RECAP_VIEW_LEADER
    assert picked is leader


def test_viewer_has_visible_recap_follows_sales_or_leader_view():
    sales = [VISIT_INSIGHT_SALES_TYPE]
    leader = [VISIT_INSIGHT_LEADER_TYPE]
    assert viewer_has_visible_recap(sales) is False
    assert viewer_has_visible_recap(sales, is_recorder=True) is True
    assert viewer_has_visible_recap(leader, is_recorder=True) is False
    assert viewer_has_visible_recap(leader, is_reporting_leader=True) is True
    assert viewer_has_visible_recap(sales, is_reporting_leader=True) is False
    assert viewer_has_visible_recap(leader, is_sys_admin=True) is True
    assert viewer_has_visible_recap(sales, is_sys_admin=True) is False
    assert viewer_has_visible_recap([]) is False


def test_load_visit_recap_types_groups_by_record_and_fails_closed():
    session = MagicMock()
    session.exec.return_value.all.return_value = [
        ("rec-1", VISIT_INSIGHT_SALES_TYPE),
        ("rec-1", VISIT_INSIGHT_LEADER_TYPE),
        ("rec-2", VISIT_INSIGHT_LEADER_TYPE),
    ]
    found = load_visit_recap_types_by_record_id(session, ["rec-1", "rec-2", "rec-1"])
    assert found["rec-1"] == {VISIT_INSIGHT_SALES_TYPE, VISIT_INSIGHT_LEADER_TYPE}
    assert found["rec-2"] == {VISIT_INSIGHT_LEADER_TYPE}

    session.exec.side_effect = RuntimeError("db down")
    assert load_visit_recap_types_by_record_id(session, ["rec-1"]) == {}
