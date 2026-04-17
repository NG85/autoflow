import pytest
from types import SimpleNamespace

pytest.importorskip("llama_index")

from app.rag.chat.review.data_retriever import (
    ReviewDataContext,
    _detect_mismatch_query_type,
    _extract_detail_query_filters,
    ReviewDataRetriever,
)
from app.rag.chat.review.intent_router import ReviewIntent, ReviewIntentRouter
from app.rag.datasource.review import ReviewDataSource
from app.rag.chat.review.indexing_policy import (
    build_review_datasource_name,
    get_or_create_review_datasource_id,
    normalize_review_data_types,
    validate_review_index_scope_by_stage,
)
from app.rag.datasource.review_format import (
    format_review_session_info,
    format_snapshot_info,
)
from app.rag.indices.knowledge_graph.crm.builder import CRMKnowledgeGraphBuilder
from app.rag.types import CrmDataType


class _FakeExecResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSnapshotDBSession:
    def __init__(self, snapshots):
        self._snapshots = snapshots

    def exec(self, _stmt):
        return _FakeExecResult(self._snapshots)


class _FakeRiskProgressDBSession:
    def __init__(self, records):
        self._records = records

    def exec(self, _stmt):
        return _FakeExecResult(self._records)


class _FakeNoopDBSession:
    def exec(self, _stmt):
        return _FakeExecResult([])


def test_soft_boundary_guard_only_applies_to_data_query():
    intent = ReviewIntent(intent_type="strategy")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "销售与AI判断不同的商机有哪些",
    )
    assert guarded.needs_clarification is False


def test_soft_boundary_guard_clarifies_ambiguous_mismatch_dimension():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "销售与AI判断不同的商机有哪些",
    )
    assert guarded.needs_clarification is True
    assert "商机阶段" in guarded.clarifying_question


def test_soft_boundary_guard_does_not_clarify_when_dimension_is_explicit():
    intent = ReviewIntent(
        intent_type="data_query",
        needs_clarification=True,
        clarifying_question="旧澄清文案",
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "项目阶段，销售判断与AI判断不一致的有哪些",
    )
    assert guarded.needs_clarification is False
    assert guarded.clarifying_question == ""


def test_soft_boundary_guard_clarifies_when_user_asks_ai_amount_mismatch():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "销售和AI金额不一致的商机有哪些",
    )
    assert guarded.needs_clarification is True
    assert "单商机快照里没有AI金额字段" in guarded.clarifying_question


def test_soft_boundary_guard_keeps_session_level_ai_amount_query():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "本期AI确定下单金额是多少",
    )
    assert guarded.needs_clarification is False


def test_detect_mismatch_query_type_stage_without_sales_keyword():
    q = "AI和商机阶段不一致的项目有哪些"
    assert _detect_mismatch_query_type(q) == "stage"


def test_detect_mismatch_query_type_stage_user_example():
    q = "项目阶段，销售判断与AI判断不一致的有哪些"
    assert _detect_mismatch_query_type(q) == "stage"


def test_detect_mismatch_query_type_forecast():
    q = "销售与AI给出不同的预测状态的有哪些商机"
    assert _detect_mismatch_query_type(q) == "forecast"


def test_detect_mismatch_query_type_close_date():
    q = "AI和销售预计成交日期不一致的商机清单"
    assert _detect_mismatch_query_type(q) == "close_date"


def test_risk_context_includes_solution_and_detail():
    ctx = ReviewDataContext(
        risks=[
            {
                "type_name": "阶段停滞",
                "summary": "多个商机推进缓慢",
                "detail_description": "关键里程碑未达成，审批链路阻塞",
                "solution": "优先处理审批卡点并设定双周推进目标",
                "severity": "HIGH",
                "financial_impact": 120000.0,
            }
        ],
        progresses=[
            {
                "type_name": "重点客户推进",
                "summary": "已完成技术澄清会",
                "solution": "在本周内推进商务条款确认",
            }
        ],
    )

    text = ctx.to_risk_context_text()
    assert "建议动作" in text
    assert "详情" in text
    assert "下一步建议" in text


def test_soft_boundary_guard_sets_query_plan_defaults():
    intent = ReviewIntent(
        intent_type="data_query",
        opportunity_name_keyword="马上消费",
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, "查看马上消费商机明细")
    assert guarded.query_type == "opportunity_detail"
    assert guarded.query_plan["route"] == "opportunity_detail"
    assert guarded.query_plan["use_kpi"] is False


def test_soft_boundary_guard_low_confidence_requires_clarification_for_detail_route():
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="opportunity_detail",
        intent_confidence=0.42,
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, "看商机明细")
    assert guarded.needs_clarification is True
    assert "确保查询准确" in guarded.clarifying_question


def test_extract_detail_filters_supports_multi_values_and_amount_range():
    filters = _extract_detail_query_filters(
        "商机里负责人是张三或李四，阶段为谈判、方案，金额在10到20万之间"
    )
    assert filters["owner_names"] == ["张三", "李四"]
    assert filters["opportunity_stages"] == ["谈判", "方案"]
    assert filters["forecast_amount_min"] == 100000
    assert filters["forecast_amount_max"] == 200000
    # range should override comparator form to avoid conflicting filters
    assert "forecast_amount_op" not in filters
    assert "forecast_amount_value" not in filters


def test_retrieve_prefers_query_plan_opportunity_detail_route(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="kpi_aggregation",
        query_plan={
            "route": "opportunity_detail",
            "detail_filters": {"owner_name": "张三"},
            "use_kpi": False,
        },
    )
    called = {"detail": 0, "kpi": 0, "agg": 0}

    def _mock_detail(*args, **kwargs):
        called["detail"] += 1
        return [{"opportunity_id": "o1", "opportunity_name": "A"}]

    monkeypatch.setattr(retriever, "_query_typical_opportunity_details", _mock_detail)
    monkeypatch.setattr(retriever, "_query_kpi_metrics", lambda *a, **k: called.__setitem__("kpi", called["kpi"] + 1) or [])
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: called.__setitem__("agg", called["agg"] + 1) or [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="查看负责人张三的商机",
    )
    assert called["detail"] == 1
    assert called["kpi"] == 0
    assert called["agg"] == 0
    assert len(ctx.opportunity_snapshot_rows) == 1


def test_retrieve_passes_current_owner_context_for_self_queries(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={
            "route": "opportunity_detail",
            "detail_filters": {"owner_name": "我"},
            "use_kpi": False,
        },
    )
    captured = {}

    def _mock_detail(*args, **kwargs):
        captured["owner_id"] = kwargs.get("current_owner_id")
        captured["owner_name"] = kwargs.get("current_owner_name")
        return []

    monkeypatch.setattr(retriever, "_query_typical_opportunity_details", _mock_detail)
    monkeypatch.setattr(retriever, "_query_kpi_metrics", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="查询我负责的商机明细",
        current_owner_id="crm_123",
        current_owner_name="张三",
    )
    assert captured["owner_id"] == "crm_123"
    assert captured["owner_name"] == "张三"


def test_retrieve_detail_query_prefers_query_plan_owner_scope_for_replay(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={
            "route": "opportunity_detail",
            "scope": {"type": "owner", "id": "owner_456", "source": "replay"},
            "detail_filters": {"requested_fields": ["owner_name"]},
            "use_kpi": False,
        },
    )
    captured = {}

    def _mock_detail(*args, **kwargs):
        captured["scope_owner_id"] = kwargs.get("scope_owner_id")
        return []

    monkeypatch.setattr(retriever, "_query_typical_opportunity_details", _mock_detail)
    monkeypatch.setattr(retriever, "_query_kpi_metrics", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="回放：看某销售商机明细",
    )
    assert captured["scope_owner_id"] == "owner_456"


def test_retrieve_mismatch_query_prefers_query_plan_owner_scope_for_replay(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={
            "route": "mismatch_list",
            "mismatch_type": "stage",
            "scope": {"type": "owner", "id": "owner_789", "source": "replay"},
            "use_kpi": False,
        },
    )
    captured = {}

    def _mock_mismatch(*args, **kwargs):
        captured["owner_id"] = kwargs.get("owner_id")
        return []

    monkeypatch.setattr(retriever, "_query_field_mismatch_opportunities", _mock_mismatch)
    monkeypatch.setattr(retriever, "_query_kpi_metrics", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="回放：看阶段差异",
    )
    assert captured["owner_id"] == "owner_789"


def test_retrieve_kpi_aggregation_prefers_query_plan_owner_scope_for_replay(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="kpi_aggregation",
        scope_type="department",
        scope_id="dept_legacy",
        query_plan={
            "route": "kpi_aggregation",
            "scope": {"type": "owner", "id": "owner_999", "source": "replay"},
            "use_kpi": True,
        },
    )
    captured = {"kpi_scope": None, "kpi_scope_id": None, "agg_scope": None, "agg_scope_id": None}

    def _mock_kpi(_db_session, _session_id, scoped_intent):
        captured["kpi_scope"] = scoped_intent.scope_type
        captured["kpi_scope_id"] = scoped_intent.scope_id
        return []

    def _mock_agg(_db_session, _snapshot_period, scoped_intent, **_kwargs):
        captured["agg_scope"] = scoped_intent.scope_type
        captured["agg_scope_id"] = scoped_intent.scope_id
        return []

    monkeypatch.setattr(retriever, "_query_kpi_metrics", _mock_kpi)
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", _mock_agg)
    monkeypatch.setattr(retriever, "_collect_opportunity_snapshot_rows", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="回放：看某销售达成概览",
    )
    assert captured["kpi_scope"] == "owner"
    assert captured["kpi_scope_id"] == "owner_999"
    assert captured["agg_scope"] == "owner"
    assert captured["agg_scope_id"] == "owner_999"


def test_retrieve_risk_progress_route_for_achievement_gap_preset(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={
            "route": "risk_progress",
            "risk_type_codes": [
                "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
                "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
            ],
            "use_kpi": False,
        },
    )
    captured = {"relation_type_names": None}

    def _mock_risk_relation(*args, **kwargs):
        captured["relation_type_names"] = kwargs.get("relation_type_names")
        return [
            {
                "opportunity_id": "opp_1",
                "opportunity_name": "华北大单",
                "type_name": "业绩达成风险",
            },
            {
                "opportunity_id": "opp_2",
                "opportunity_name": "华东续签",
                "type_name": "业绩达成风险",
            },
        ]

    monkeypatch.setattr(retriever, "_query_kpi_metrics", lambda *a, **k: pytest.fail("kpi should not be called"))
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: pytest.fail("agg should not be called"))
    monkeypatch.setattr(retriever, "_query_typical_opportunity_details", lambda *a, **k: pytest.fail("detail should not be called"))
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: pytest.fail("opp risk chain should not be called"))
    monkeypatch.setattr(retriever, "_query_risk_opportunity_relations", _mock_risk_relation)

    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="当前业绩有哪些达成风险？",
    )
    assert captured["relation_type_names"] == ["业绩达成风险"]
    assert len(ctx.risks) == 2
    assert "风险与对象：风险类型为有高风险commit商机、commit商机储备不足风险" in (ctx.query_note or "")
    assert "2 个达成风险商机" in (ctx.query_note or "")
    assert "华北大单、华东续签" in (ctx.query_note or "")
    assert "达成风险卡片" in (ctx.query_note or "")


def test_retrieve_risk_progress_counts_unique_opportunities(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={
            "route": "risk_progress",
            "risk_type_codes": [
                "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
                "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
            ],
            "use_kpi": False,
        },
    )

    monkeypatch.setattr(
        retriever,
        "_query_risk_opportunity_relations",
        lambda *a, **k: [
            {
                "opportunity_id": "opp_1",
                "opportunity_name": "华北大单",
                "type_name": "业绩达成风险",
            },
            {
                "opportunity_id": "opp_1",
                "opportunity_name": "华北大单",
                "type_name": "业绩达成风险",
            },
        ],
    )
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: pytest.fail("opp risk chain should not be called"))
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="当前业绩有哪些达成风险？",
    )
    assert "1 个达成风险商机" in (ctx.query_note or "")


def test_retrieve_risk_progress_filters_type_codes_by_risk_universe(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={
            "route": "risk_progress",
            "risk_type_codes": ["NOT_A_REAL_CODE"],
            "use_kpi": False,
        },
    )
    captured = {}

    def _mock_risk_relation(*args, **kwargs):
        captured["relation_type_names"] = kwargs.get("relation_type_names")
        return []

    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: pytest.fail("opp risk chain should not be called"))
    monkeypatch.setattr(retriever, "_query_risk_opportunity_relations", _mock_risk_relation)
    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="当前业绩有哪些达成风险？",
    )
    assert captured["relation_type_names"] == ["业绩达成风险"]


def test_retrieve_risk_progress_opportunity_level_types_use_opp_risk_chain(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={
            "route": "risk_progress",
            "risk_type_codes": ["CUSTOMER_DECISION_RISK"],
            "use_kpi": False,
        },
    )
    captured = {}
    monkeypatch.setattr(
        "app.rag.chat.review.data_retriever.load_risk_type_name_map",
        lambda _db, _codes=None: {"CUSTOMER_DECISION_RISK": "客户决策风险"},
    )

    def _mock_risk_progress(*args, **kwargs):
        captured["risk_type_codes"] = kwargs.get("risk_type_codes")
        return [
            {
                "record_type": "RISK",
                "type_code": "CUSTOMER_DECISION_RISK",
                "opportunity_id": "opp_x",
                "opportunity_name": "客户A续签",
            }
        ]

    monkeypatch.setattr(retriever, "_query_risk_opportunity_relations", lambda *a, **k: pytest.fail("business risk chain should not be called"))
    monkeypatch.setattr(retriever, "_query_risk_progress", _mock_risk_progress)
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="当前业绩有哪些达成风险？",
    )
    assert captured["risk_type_codes"] == ["CUSTOMER_DECISION_RISK"]
    assert "商机风险卡片" in (ctx.query_note or "")


def test_retrieve_target_action_template_uses_commit_high_risk_when_commit_gt_gap(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={"route": "risk_progress", "template_id": "target_action_to_hit_goal"},
    )
    monkeypatch.setattr(
        "app.rag.chat.review.data_retriever.load_risk_type_name_map",
        lambda _db, _codes=None: {
            "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK": "有高风险commit商机",
            "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT": "commit商机储备不足风险",
        },
    )
    monkeypatch.setattr(
        retriever,
        "_resolve_target_action_risk_type_codes",
        lambda **kwargs: ["ACHIEVEMENT_GAP_COMMIT_HIGH_RISK"],
    )
    captured = {}
    def _mock_risk_relation(*args, **kwargs):
        captured["relation_type_names"] = kwargs.get("relation_type_names")
        return []
    monkeypatch.setattr(retriever, "_query_risk_opportunity_relations", _mock_risk_relation)
    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="需要做什么才能达成目标？",
    )
    assert captured["relation_type_names"] == ["业绩达成风险"]


def test_retrieve_target_action_template_uses_upside_insufficient_when_commit_lt_gap(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={"route": "risk_progress", "template_id": "target_action_to_hit_goal"},
    )
    monkeypatch.setattr(
        "app.rag.chat.review.data_retriever.load_risk_type_name_map",
        lambda _db, _codes=None: {
            "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK": "有高风险commit商机",
            "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT": "commit商机储备不足风险",
        },
    )
    monkeypatch.setattr(
        retriever,
        "_resolve_target_action_risk_type_codes",
        lambda **kwargs: ["ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT"],
    )
    captured = {}
    def _mock_risk_relation(*args, **kwargs):
        captured["relation_type_names"] = kwargs.get("relation_type_names")
        return []
    monkeypatch.setattr(retriever, "_query_risk_opportunity_relations", _mock_risk_relation)
    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="需要做什么才能达成目标？",
    )
    assert captured["relation_type_names"] == ["业绩达成风险"]


def test_retrieve_target_action_template_prioritizes_solution_actions(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={"route": "risk_progress", "template_id": "target_action_to_hit_goal"},
    )
    monkeypatch.setattr(
        "app.rag.chat.review.data_retriever.load_risk_type_name_map",
        lambda _db, _codes=None: {"CUSTOMER_DECISION_RISK": "客户决策风险"},
    )
    monkeypatch.setattr(
        retriever,
        "_resolve_target_action_risk_type_codes",
        lambda **kwargs: ["CUSTOMER_DECISION_RISK"],
    )
    monkeypatch.setattr(
        retriever,
        "_query_risk_opportunity_relations",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        retriever,
        "_query_risk_progress",
        lambda *a, **k: [
            {
                "record_type": "RISK",
                "type_code": "CUSTOMER_DECISION_RISK",
                "opportunity_name": "华北大单",
                "solution": "推动关键决策人共识会，锁定下周决策时间窗",
            }
        ],
    )
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="需要做什么才能达成目标？",
    )
    assert "### 达成目标建议" in (ctx.query_note or "")
    assert "建议动作（优先执行）" in (ctx.query_note or "")
    assert "推动关键决策人共识会" in (ctx.query_note or "")


def test_router_respects_frontend_preset_template_for_achievement_risk():
    intent = ReviewIntent(
        intent_type="data_query",
        preset_template="achievement_risk_overview",
        query_type="kpi_aggregation",
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, "任意文案")
    assert guarded.query_type == "risk_progress"
    assert guarded.query_plan["template_id"] == "achievement_risk_overview"
    assert guarded.query_plan["route"] == "risk_progress"
    assert guarded.query_plan["risk_type_codes"] == [
        "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
        "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
    ]


def test_router_template_params_filters_achievement_risk_type_codes():
    intent = ReviewIntent(
        intent_type="data_query",
        preset_template="achievement_risk_overview",
        template_params={
            "risk_type_codes": [
                "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
                "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
                "UNKNOWN_CODE",
            ],
        },
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, "x")
    assert guarded.query_plan["risk_type_codes"] == [
        "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
        "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
        "UNKNOWN_CODE",
    ]


def test_router_template_params_empty_list_falls_back_to_default_achievement_codes():
    intent = ReviewIntent(
        intent_type="data_query",
        preset_template="achievement_risk_overview",
        template_params={"risk_type_codes": []},
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, "x")
    assert guarded.query_plan["risk_type_codes"] == [
        "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
        "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
    ]


def test_router_template_params_only_invalid_codes_are_kept_for_retriever_validation():
    intent = ReviewIntent(
        intent_type="data_query",
        preset_template="achievement_risk_overview",
        template_params={"risk_type_codes": ["NOT_A_REAL_CODE"]},
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, "x")
    assert guarded.query_plan["risk_type_codes"] == ["NOT_A_REAL_CODE"]


def test_router_sets_target_action_template_route():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, "需要做什么才能达成目标？")
    assert guarded.query_type == "risk_progress"
    assert guarded.preset_template == "target_action_to_hit_goal"
    assert guarded.query_plan["template_id"] == "target_action_to_hit_goal"
    assert guarded.query_plan["route"] == "risk_progress"
    assert guarded.query_plan["plan_version"] == "v1"
    assert guarded.query_plan["scope"]["source"] == "template"


def test_router_sets_owner_gap_ranking_template_route():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "目前团队内每个销售的业绩达成情况，谁差的比较多？",
    )
    assert guarded.query_type == "kpi_aggregation"
    assert guarded.preset_template == "owner_gap_ranking"
    assert guarded.query_plan["template_id"] == "owner_gap_ranking"
    assert guarded.query_plan["route"] == "kpi_aggregation"


def test_router_sets_focus_risky_opportunities_template_route():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "哪些商机需要重点关注？",
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.preset_template == "focus_risky_opportunities"
    assert guarded.query_plan["template_id"] == "focus_risky_opportunities"
    assert guarded.query_plan["route"] == "risk_progress"


def test_retrieve_owner_gap_ranking_uses_desc_gap_order(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={"route": "kpi_aggregation", "template_id": "owner_gap_ranking"},
    )
    monkeypatch.setattr(
        retriever,
        "_query_owner_gap_ranking",
        lambda *a, **k: [
            {"scope_name": "王五", "metric_name": "gap", "metric_value": 120000.0},
            {"scope_name": "李四", "metric_name": "gap", "metric_value": 90000.0},
            {"scope_name": "张三", "metric_name": "gap", "metric_value": 50000.0},
        ],
    )
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_collect_opportunity_snapshot_rows", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="目前团队内每个销售的业绩达成情况，谁差的比较多？",
    )
    assert [m["scope_name"] for m in ctx.kpi_metrics] == ["王五", "李四", "张三"]
    assert "已按达成差额从高到低排序" in (ctx.query_note or "")
    assert "目前差额最大的是 王五" in (ctx.query_note or "")
    assert "可进一步对比的销售：李四、张三" in (ctx.query_note or "")
    assert "查看路径：点击商机评估Agent，选择“人员分组”" in (ctx.query_note or "")


def test_retrieve_focus_risky_opportunities_lists_risk_opps(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={"route": "risk_progress", "template_id": "focus_risky_opportunities"},
    )
    monkeypatch.setattr(
        retriever,
        "_query_risk_progress",
        lambda *a, **k: [
            {"record_type": "RISK", "opportunity_name": "华北大单"},
            {"record_type": "PROGRESS", "opportunity_name": "华北大单"},
            {"record_type": "RISK", "opportunity_name": "华东续签"},
        ],
    )
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="哪些商机需要重点关注？",
    )
    assert len(ctx.risks) == 2
    assert "共识别到 2 个需重点关注的风险商机" in (ctx.query_note or "")
    assert "涉及商机：华北大单、华东续签" in (ctx.query_note or "")
    assert "查看路径：点击商机评估Agent，筛选有风险的商机，点击商机详情。" in (ctx.query_note or "")


def test_router_sets_opportunity_risk_taxonomy_template_route():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "目前商机都存在哪些风险？",
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.preset_template == "opportunity_risk_taxonomy"
    assert guarded.query_plan["template_id"] == "opportunity_risk_taxonomy"
    assert guarded.query_plan["route"] == "risk_progress"


def test_router_non_template_risk_query_auto_scopes_to_department():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "部门目前有哪些风险？",
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.scope_type == "department"
    assert guarded.query_plan["scope"]["type"] == "department"
    assert guarded.query_plan["scope"]["source"] == "auto_inferred"
    assert guarded.query_plan["plan_version"] == "v1"
    assert guarded.query_plan["scope"]["type"] == "department"
    assert guarded.query_plan["time_scope"]["mode"] == "current_only"
    assert guarded.needs_clarification is False


def test_router_non_template_risk_query_auto_scopes_to_opportunity():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "商机侧风险情况怎么样？",
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.scope_type == "opportunity"
    assert guarded.query_plan["scope"]["type"] == "opportunity"
    assert guarded.query_plan["scope"]["source"] == "auto_inferred"
    assert guarded.needs_clarification is False


def test_router_non_template_risk_query_self_owner_scope():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "我负责的商机有哪些风险？",
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.scope_type == "owner"
    assert guarded.scope_id == "__CURRENT_USER__"
    assert guarded.query_plan["scope"]["type"] == "owner"


def test_router_non_template_risk_query_named_owner_scope():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "销售张三有哪些风险？",
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.scope_type == "owner"
    assert guarded.detail_filters.get("owner_name") == "张三"
    assert guarded.query_plan["scope"]["type"] == "owner"


def test_router_non_template_risk_query_asks_clarification_when_scope_ambiguous():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "目前有哪些风险？",
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.scope_type is None
    assert guarded.query_plan["scope"]["type"] is None
    assert guarded.query_plan["scope"]["source"] == "unspecified"
    assert guarded.needs_clarification is True
    assert "部门/公司层面风险" in guarded.clarifying_question


def test_router_non_template_risk_query_followup_short_scope_reply_uses_history():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "看商机/客户层面",
        chat_history=[
            {"role": "assistant", "content": "你更想看哪一类风险：部门/公司层面风险，还是商机/客户层面风险？"}
        ],
    )
    assert guarded.query_type == "risk_progress"
    assert guarded.scope_type == "opportunity"
    assert guarded.needs_clarification is False
    assert guarded.query_plan["scope"]["type"] == "opportunity"
    assert guarded.query_plan["scope"]["source"] == "auto_inferred"


def test_router_clarifies_when_question_requests_non_current_period():
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "和上期比，哪些商机风险变化最大？",
    )
    assert guarded.needs_clarification is True
    assert "只支持本期数据" in guarded.clarifying_question
    assert guarded.time_comparison == "current_only"


def test_router_normalizes_query_plan_contract_when_input_is_partial_or_dirty():
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="risk_progress",
        scope_type="owner",
        scope_id="owner_123",
        query_plan={
            "route": "not_a_route",
            "scope": {"type": "not_a_scope", "id": "owner_999"},
            "time_scope": {"mode": "not_a_time_mode"},
        },
    )
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "我负责的商机有哪些风险？",
    )
    assert guarded.query_plan["plan_version"] == "v1"
    assert guarded.query_plan["route"] == "risk_progress"
    assert guarded.query_plan["scope"]["type"] == "owner"
    assert guarded.query_plan["scope"]["id"] == "owner_123"
    assert guarded.query_plan["scope"]["source"] == "auto_inferred"
    assert guarded.query_plan["time_scope"]["mode"] == "current_only"
    assert guarded.query_plan["time_scope"]["source"] == "intent"


def test_retrieve_opportunity_risk_taxonomy_groups_by_category(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_plan={"route": "risk_progress", "template_id": "opportunity_risk_taxonomy"},
    )
    monkeypatch.setattr(
        "app.rag.chat.review.data_retriever.load_risk_code_meta",
        lambda _db: {
            "RISK_A": {
                "name_zh": "风险甲",
                "category_group": "客户类",
                "sort_order": 1,
            },
            "RISK_B": {
                "name_zh": "风险乙",
                "category_group": "客户类",
                "sort_order": 2,
            },
            "RISK_C": {
                "name_zh": "风险丙",
                "category_group": "交付类",
                "sort_order": 3,
            },
        },
    )
    monkeypatch.setattr(
        retriever,
        "_query_risk_progress",
        lambda *a, **k: [
            {
                "record_type": "RISK",
                "type_code": "RISK_A",
                "opportunity_id": "o1",
                "opportunity_name": "华北大单",
            },
            {
                "record_type": "RISK",
                "type_code": "RISK_A",
                "opportunity_id": "o2",
                "opportunity_name": "华南项目",
            },
            {
                "record_type": "RISK",
                "type_code": "RISK_B",
                "opportunity_id": "o2",
                "opportunity_name": "华南项目",
            },
            {
                "record_type": "RISK",
                "type_code": "RISK_C",
                "opportunity_id": "o3",
                "opportunity_name": "华东续签",
            },
            {"record_type": "PROGRESS", "type_code": "RISK_A", "opportunity_id": "o1"},
        ],
    )
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="目前商机都存在哪些风险？",
    )
    assert ctx.risks == []
    assert len(ctx.risk_category_breakdown) == 2
    by_cat = {b["category_group"]: b for b in ctx.risk_category_breakdown}
    assert by_cat["客户类"]["opportunity_count"] == 2
    assert set(by_cat["客户类"]["opportunity_names"]) == {"华北大单", "华南项目"}
    assert by_cat["交付类"]["opportunity_count"] == 1
    assert "按配置表风险类别汇总" in (ctx.query_note or "")
    assert "客户类" in (ctx.query_note or "")
    assert "风险甲（RISK_A）" in (ctx.query_note or "")
    assert "查看路径：点击商机评估Agent，筛选有风险的商机，点击商机详情。" in (ctx.query_note or "")


def test_retrieve_non_template_department_scope_prefers_business_risks(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(intent_type="data_query", query_type="risk_progress", scope_type="department")
    called = {}

    def _mock_relation(*args, **kwargs):
        called["relation"] = True
        return [{"record_type": "RISK", "type_name": "业绩达成风险", "opportunity_name": "华北大单"}]

    monkeypatch.setattr(retriever, "_query_risk_opportunity_relations", _mock_relation)
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: pytest.fail("opp chain should not be called"))
    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="部门目前有哪些风险？",
    )
    assert called.get("relation") is True


def test_retrieve_non_template_opportunity_scope_uses_opp_risk_chain(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(intent_type="data_query", query_type="risk_progress", scope_type="opportunity")
    monkeypatch.setattr(retriever, "_query_risk_opportunity_relations", lambda *a, **k: pytest.fail("relation chain should not be called"))
    monkeypatch.setattr(
        retriever,
        "_query_risk_progress",
        lambda *a, **k: [{"record_type": "RISK", "opportunity_name": "华东续签"}],
    )
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="商机/客户层面都有哪些风险？",
    )
    assert "范围为商机/客户层面风险" in (ctx.query_note or "")
    assert "华东续签" in (ctx.query_note or "")


def test_retrieve_non_template_owner_scope_uses_current_owner_id(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="risk_progress",
        scope_type="owner",
        scope_id="__CURRENT_USER__",
    )
    captured = {}

    def _mock_risk_progress(_db, _sid, _period, eff_intent, risk_type_codes=None):
        captured["scope_type"] = eff_intent.scope_type
        captured["scope_id"] = eff_intent.scope_id
        return [{"record_type": "RISK", "opportunity_name": "华东续签"}]

    monkeypatch.setattr(
        retriever,
        "_query_risk_opportunity_relations",
        lambda *a, **k: pytest.fail("relation chain should not be called"),
    )
    monkeypatch.setattr(retriever, "_query_risk_progress", _mock_risk_progress)
    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="我负责的商机有哪些风险？",
        current_owner_id="owner_123",
    )
    assert captured["scope_type"] == "owner"
    assert captured["scope_id"] == "owner_123"


def test_retrieve_non_template_owner_scope_prefers_query_plan_scope_for_replay(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="risk_progress",
        scope_type=None,
        scope_id=None,
        query_plan={
            "route": "risk_progress",
            "scope": {"type": "owner", "id": "owner_456", "source": "replay"},
        },
    )
    captured = {}

    def _mock_risk_progress(_db, _sid, _period, eff_intent, risk_type_codes=None):
        captured["scope_type"] = eff_intent.scope_type
        captured["scope_id"] = eff_intent.scope_id
        return [{"record_type": "RISK", "opportunity_name": "华东续签"}]

    monkeypatch.setattr(
        retriever,
        "_query_risk_opportunity_relations",
        lambda *a, **k: pytest.fail("relation chain should not be called"),
    )
    monkeypatch.setattr(retriever, "_query_risk_progress", _mock_risk_progress)
    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="回放：看指定销售风险",
    )
    assert captured["scope_type"] == "owner"
    assert captured["scope_id"] == "owner_456"


def test_retrieve_non_template_owner_scope_named_owner_not_found_returns_clarify_note(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="risk_progress",
        scope_type="owner",
        detail_filters={"owner_name": "张三"},
    )
    monkeypatch.setattr(
        retriever,
        "_resolve_owner_id_by_name",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        retriever,
        "_query_risk_progress",
        lambda *a, **k: pytest.fail("risk query should not be called when owner unresolved"),
    )
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="销售张三有哪些风险？",
    )
    assert "需确认销售人员" in (ctx.query_note or "")
    assert "暂未识别到“张三”对应的销售人员" in (ctx.query_note or "")


def test_retrieve_rejects_non_current_time_scope_from_query_plan():
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="risk_progress",
        query_plan={
            "route": "risk_progress",
            "time_scope": {"mode": "wow", "source": "replay"},
        },
    )
    ctx = retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="环比看下风险变化",
    )
    assert "当前 review 问答先只支持本期数据" in (ctx.query_note or "")


def test_retrieve_normalizes_dirty_query_plan_scope_and_time_for_execution(monkeypatch):
    retriever = ReviewDataRetriever()
    review_session = SimpleNamespace(unique_id="s1", period="2026-W15", department_id="d1")
    intent = ReviewIntent(
        intent_type="data_query",
        query_type="kpi_aggregation",
        scope_type="owner",
        scope_id="__CURRENT_USER__",
        query_plan={
            "route": "unknown_route",
            "scope": "invalid",
            "time_scope": {"mode": "invalid_mode"},
            "use_kpi": True,
        },
    )
    captured = {"scope_type": None, "scope_id": None}

    def _mock_kpi(_db_session, _session_id, scoped_intent):
        captured["scope_type"] = scoped_intent.scope_type
        captured["scope_id"] = scoped_intent.scope_id
        return []

    monkeypatch.setattr(retriever, "_query_kpi_metrics", _mock_kpi)
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_collect_opportunity_snapshot_rows", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    retriever.retrieve(
        db_session=None,
        review_session=review_session,
        intent=intent,
        user_question="看我负责范围的达成情况",
        current_owner_id="owner_abc",
    )
    assert captured["scope_type"] == "owner"
    assert captured["scope_id"] == "owner_abc"


@pytest.mark.parametrize(
    "question,expected_route,expected_mismatch",
    [
        (
            "列出销售商机阶段与AI商机阶段不一致的商机清单",
            "mismatch_list",
            "stage",
        ),
        (
            "列出销售预测状态与AI预测状态不一致的商机清单",
            "mismatch_list",
            "forecast",
        ),
        (
            "列出销售预计成交日期与AI预计成交日期不一致的商机清单",
            "mismatch_list",
            "close_date",
        ),
        (
            "查询负责人是张三的商机明细",
            "opportunity_detail",
            None,
        ),
        (
            "查询签约金额大于50万的商机列表",
            "opportunity_detail",
            None,
        ),
        (
            "当前业绩有哪些达成风险？",
            "risk_progress",
            None,
        ),
        (
            "哪些商机需要重点关注？",
            "risk_progress",
            None,
        ),
        (
            "目前商机都存在哪些风险？",
            "risk_progress",
            None,
        ),
    ],
)
def test_preset_question_router_guardrails(question, expected_route, expected_mismatch):
    intent = ReviewIntent(intent_type="data_query")
    guarded = ReviewIntentRouter._apply_soft_boundary_guard(intent, question)
    assert guarded.needs_clarification is False
    assert guarded.query_type == expected_route
    assert guarded.query_plan["route"] == expected_route
    if expected_mismatch is not None:
        assert guarded.mismatch_type == expected_mismatch
        assert guarded.query_plan["mismatch_type"] == expected_mismatch
    if expected_route == "risk_progress":
        if question == "当前业绩有哪些达成风险？":
            assert guarded.preset_template == "achievement_risk_overview"
            assert guarded.query_plan["template_id"] == "achievement_risk_overview"
            assert guarded.query_plan["risk_type_codes"] == [
                "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
                "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
            ]
        elif question == "哪些商机需要重点关注？":
            assert guarded.preset_template == "focus_risky_opportunities"
            assert guarded.query_plan["template_id"] == "focus_risky_opportunities"
        elif question == "目前商机都存在哪些风险？":
            assert guarded.preset_template == "opportunity_risk_taxonomy"
            assert guarded.query_plan["template_id"] == "opportunity_risk_taxonomy"


@pytest.mark.parametrize(
    "question,expected_filters",
    [
        (
            "查询负责人是张三的商机明细",
            {"owner_name": "张三"},
        ),
        (
            "查询阶段为谈判或方案的商机列表",
            {"opportunity_stages": ["谈判", "方案"]},
        ),
        (
            "查询签约金额大于50万的商机列表",
            {"forecast_amount_op": "gt", "forecast_amount_value": 500000},
        ),
        (
            "查询签约金额在10到20万之间的商机列表",
            {"forecast_amount_min": 100000, "forecast_amount_max": 200000},
        ),
    ],
)
def test_preset_question_detail_filter_guardrails(question, expected_filters):
    filters = _extract_detail_query_filters(question)
    for key, expected in expected_filters.items():
        assert filters.get(key) == expected


def test_review_index_scope_disallow_snapshot_in_initial_edit():
    with pytest.raises(ValueError):
        validate_review_index_scope_by_stage(
            "initial_edit",
            ["crm_review_snapshot"],
        )


@pytest.mark.parametrize("stage", ["initial_edit", "first_calculating"])
def test_review_index_scope_disallow_risk_before_first_calc_ready(stage):
    with pytest.raises(ValueError):
        validate_review_index_scope_by_stage(
            stage,
            ["crm_review_risk_progress"],
        )


@pytest.mark.parametrize("stage", ["first_calc_ready", "lead_review", "second_calculating", "completed"])
def test_review_index_scope_allow_risk_from_first_calc_ready(stage):
    validate_review_index_scope_by_stage(
        stage,
        ["crm_review_risk_progress"],
    )


def test_normalize_review_data_types_defaults_to_session_snapshot_and_risk():
    assert normalize_review_data_types(None) == [
        "crm_review_session",
        "crm_review_snapshot",
        "crm_review_risk_progress",
    ]


def test_build_review_datasource_name_by_session():
    assert build_review_datasource_name("rs_2026_w15_dept_a") == (
        "CRM Review Session (rs_2026_)"
    )


def test_build_review_datasource_name_prefer_session_name_with_id_suffix():
    assert build_review_datasource_name(
        "rs_2026_w15_dept_a",
        session_name="华东大区W15复盘",
    ) == "CRM Review Session (华东大区W15复盘 | rs_2026_)"


def test_get_or_create_review_datasource_id_reuse_same_session():
    ds_name = build_review_datasource_name("rs_a")
    existing_ds = SimpleNamespace(id=101, name=ds_name, deleted_at=None)
    kb = SimpleNamespace(id=1, data_sources=[existing_ds])
    db_session = SimpleNamespace()
    ds_id = get_or_create_review_datasource_id(db_session, kb, session_id="rs_a")
    assert ds_id == 101


def test_get_or_create_review_datasource_id_separate_by_session():
    # Existing datasource belongs to another session and should not be reused.
    existing_ds = SimpleNamespace(
        id=101,
        name=build_review_datasource_name("rs_a"),
        deleted_at=None,
    )
    kb = SimpleNamespace(id=1, data_sources=[existing_ds])

    added = []

    class DummyDBSession:
        def add(self, obj):
            # Simulate autoincrement id assignment for new DataSource before refresh.
            if getattr(obj, "id", None) is None and obj.__class__.__name__ == "DataSource":
                obj.id = 202
            added.append(obj)

        def flush(self):
            return None

        def commit(self):
            return None

        def refresh(self, obj):
            return None

    db_session = DummyDBSession()
    ds_id = get_or_create_review_datasource_id(db_session, kb, session_id="rs_b")
    assert ds_id == 202
    created_ds = next(o for o in added if o.__class__.__name__ == "DataSource")
    assert created_ds.name == build_review_datasource_name("rs_b")


def test_get_or_create_review_datasource_id_ignores_deleted_datasource():
    ds_name = build_review_datasource_name("rs_a")
    deleted_ds = SimpleNamespace(id=101, name=ds_name, deleted_at="2026-04-01T00:00:00Z")
    kb = SimpleNamespace(id=1, data_sources=[deleted_ds])

    added = []

    class DummyDBSession:
        def add(self, obj):
            if getattr(obj, "id", None) is None and obj.__class__.__name__ == "DataSource":
                obj.id = 303
            added.append(obj)

        def flush(self):
            return None

        def commit(self):
            return None

        def refresh(self, obj):
            return None

    db_session = DummyDBSession()
    ds_id = get_or_create_review_datasource_id(db_session, kb, session_id="rs_a")
    assert ds_id == 303
    created_ds = next(o for o in added if o.__class__.__name__ == "DataSource")
    assert created_ds.name == ds_name


def test_review_chunk_fact_extraction_produces_chunk_specific_has_fact_relations():
    builder = CRMKnowledgeGraphBuilder()
    primary_data = {
        "session_id": "rs_1",
        "session_name": "华东大区W15复盘",
        "department_name": "银行二部",
        "period": "2026-W15",
        "stage": "first_calc_ready",
    }
    meta = {
        "crm_data_type": CrmDataType.REVIEW_SESSION,
        "session_id": "rs_1",
        "stage": "first_calc_ready",
    }

    chunk_text_a = (
        "- [负责人范围] 范围类型=负责人；范围名称=张三；指标=销售确定下单 (commit_sales)；"
        "当前值=100；上期值=90；变化量=10；变化率=11.1%。"
    )
    chunk_text_b = (
        "- [负责人范围] 范围类型=负责人；范围名称=李四；指标=销售确定下单 (commit_sales)；"
        "当前值=80；上期值=100；变化量=-20；变化率=-20.0%。"
    )

    _, rels_a = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_SESSION,
        primary_data=primary_data,
        secondary_data={},
        document_id="doc_1",
        chunk_id="chunk_a",
        meta=meta,
        chunk_text=chunk_text_a,
    )
    _, rels_b = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_SESSION,
        primary_data=primary_data,
        secondary_data={},
        document_id="doc_1",
        chunk_id="chunk_b",
        meta=meta,
        chunk_text=chunk_text_b,
    )

    facts_a = [r for r in rels_a if r.get("meta", {}).get("relation_type") == "HAS_FACT"]
    facts_b = [r for r in rels_b if r.get("meta", {}).get("relation_type") == "HAS_FACT"]
    dept_rels_a = [
        r
        for r in rels_a
        if r.get("meta", {}).get("relation_type") == "BELONGS_TO"
        and r.get("target_entity") == "银行二部"
    ]
    assert len(facts_a) == 1
    assert len(facts_b) == 1
    assert len(dept_rels_a) == 1
    assert dept_rels_a[0]["meta"]["target_type"] == CrmDataType.DEPARTMENT
    assert facts_a[0]["meta"]["fact_scope_name"] == "张三"
    assert facts_b[0]["meta"]["fact_scope_name"] == "李四"
    assert facts_a[0]["meta"]["session_id"] == "rs_1"
    assert facts_b[0]["meta"]["session_id"] == "rs_1"
    assert facts_a[0]["meta"]["snapshot_period"] == "2026-W15"
    assert facts_b[0]["meta"]["snapshot_period"] == "2026-W15"
    assert facts_a[0]["meta"]["stage"] == "first_calc_ready"
    assert facts_b[0]["meta"]["stage"] == "first_calc_ready"
    assert facts_a[0]["meta"]["fact_metric_key"] == "commit_sales"
    assert facts_b[0]["meta"]["fact_metric_key"] == "commit_sales"
    assert facts_a[0]["meta"]["fact_hash"]
    assert facts_b[0]["meta"]["fact_hash"]
    assert facts_a[0]["meta"]["fact_hash"] != facts_b[0]["meta"]["fact_hash"]
    assert facts_a[0]["meta"]["chunk_id"] == "chunk_a"
    assert facts_b[0]["meta"]["chunk_id"] == "chunk_b"
    assert facts_a[0]["relationship_desc"] != facts_b[0]["relationship_desc"]


def test_review_session_department_relation_only_on_primary_chunk():
    builder = CRMKnowledgeGraphBuilder()
    primary_data = {
        "session_id": "rs_1",
        "session_name": "华东大区W15复盘",
        "department_name": "银行二部",
        "period": "2026-W15",
        "stage": "first_calc_ready",
    }
    meta_non_primary = {
        "crm_data_type": CrmDataType.REVIEW_SESSION,
        "session_id": "rs_1",
        "is_primary_chunk_for_document": False,
    }
    _, rels = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_SESSION,
        primary_data=primary_data,
        secondary_data={},
        document_id="doc_1",
        chunk_id="chunk_b",
        meta=meta_non_primary,
        chunk_text="- [部门范围] 部门=银行二部；指标=已成单 (closed)；当前值=0；上期值=0；变化量=0；变化率=0.0%。",
    )
    dept_rels = [
        r
        for r in rels
        if r.get("meta", {}).get("relation_type") == "BELONGS_TO"
        and r.get("target_entity") == "银行二部"
    ]
    assert dept_rels == []


def test_review_snapshot_adds_sales_ai_match_relations():
    builder = CRMKnowledgeGraphBuilder()
    primary_data = {
        "unique_id": "snap_1",
        "session_id": "rs_1",
        "snapshot_period": "2026-W15",
        "opportunity_id": "opp_1",
        "opportunity_name": "某重点商机",
        "account_id": "acc_1",
        "account_name": "某重点客户",
        "owner_name": "张三",
        "forecast_type": "commit",
        "opportunity_stage": "谈判",
        "expected_closing_date": "2026-04-20",
        "ai_commit": "upside",
        "ai_stage": "方案",
        "ai_expected_closing_date": "2026-05-01",
    }
    _, rels = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_SNAPSHOT,
        primary_data=primary_data,
        secondary_data={"session_id": "rs_1", "session_name": "W15复盘"},
        document_id="doc_1",
        chunk_id="chunk_1",
        meta={"crm_data_type": CrmDataType.REVIEW_SNAPSHOT, "session_id": "rs_1"},
        chunk_text="",
    )
    rel_type_to_state = {
        r.get("meta", {}).get("relation_type"): r.get("meta", {}).get("match_state")
        for r in rels
        if r.get("meta", {}).get("relation_type", "").startswith("SALES_AI_")
    }
    assert rel_type_to_state["SALES_AI_FORECAST_MATCH"] == "不一致"
    assert rel_type_to_state["SALES_AI_STAGE_MATCH"] == "不一致"
    assert rel_type_to_state["SALES_AI_CLOSE_DATE_MATCH"] == "不一致"
    customer_rels = [
        r
        for r in rels
        if r.get("meta", {}).get("relation_type") == "BELONGS_TO_CUSTOMER"
    ]
    assert len(customer_rels) == 1
    assert customer_rels[0]["target_entity"] == "某重点客户"
    assert customer_rels[0]["meta"]["account_id"] == "acc_1"
    snapshot_of_rel = next(
        r for r in rels if r.get("meta", {}).get("relation_type") == "SNAPSHOT_OF"
    )
    assert snapshot_of_rel["meta"]["opportunity_id"] == "opp_1"


def test_review_snapshot_fallback_uses_placeholder_name_not_id():
    builder = CRMKnowledgeGraphBuilder()
    opp_id = "6785154dea998b00015b2933"
    primary_data = {
        "unique_id": "snap_2",
        "session_id": "rs_2",
        "snapshot_period": "2026-W11",
        "opportunity_id": opp_id,
        "forecast_type": "commit",
    }
    entities, rels = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_SNAPSHOT,
        primary_data=primary_data,
        secondary_data={"session_id": "rs_2", "session_name": "W11复盘"},
        document_id="doc_2",
        chunk_id="chunk_2",
        meta={"crm_data_type": CrmDataType.REVIEW_SNAPSHOT, "session_id": "rs_2"},
        chunk_text="",
    )

    snapshot_entity = next(e for e in entities if e.get("meta", {}).get("snapshot_period") == "2026-W11")
    assert snapshot_entity["name"] == "未命名商机_2026-W11"
    assert opp_id not in snapshot_entity["description"]

    opp_entity = next(e for e in entities if e.get("meta", {}).get("opportunity_id") == opp_id and e["name"] == "未命名商机")
    assert opp_entity["meta"]["opportunity_id"] == opp_id

    snapshot_of_rel = next(r for r in rels if r.get("meta", {}).get("relation_type") == "SNAPSHOT_OF")
    assert opp_id not in snapshot_of_rel["relationship_desc"]
    assert snapshot_of_rel["target_entity"] == "未命名商机"


def test_review_snapshot_document_metadata_includes_opportunity_name():
    snapshot = SimpleNamespace(
        unique_id="snap_meta_1",
        opportunity_id="opp_meta_1",
        opportunity_name="华东重点商机",
        account_id="acc_1",
        account_name="重点客户A",
        owner_id="owner_1",
        owner_name="张三",
        snapshot_period="2026-W11",
        forecast_type="commit",
        opportunity_stage="谈判",
        expected_closing_date="2026-04-20",
        baseline_forecast_type=None,
        baseline_forecast_amount=None,
        baseline_opportunity_stage=None,
        baseline_expected_closing_date=None,
        ai_commit=None,
        ai_stage=None,
        ai_expected_closing_date=None,
        forecast_amount=None,
        stage_stay=None,
        was_modified=False,
        modification_count=0,
    )
    fake_db = _FakeSnapshotDBSession([snapshot])
    ds = ReviewDataSource(
        db_session=fake_db,
        knowledge_base_id=1,
        data_source_id=1,
        user_id=1,
        review_session_id="rs_meta_1",
    )
    ds._get_session_attendee_owner_ids = lambda _session_id: ["owner_1"]
    session_obj = SimpleNamespace(unique_id="rs_meta_1", period="2026-W11", session_name="华东大区W11复盘")

    docs = list(ds._load_snapshot_documents(session_obj))
    assert len(docs) == 1
    assert docs[0].meta["opportunity_id"] == "opp_meta_1"
    assert docs[0].meta["opportunity_name"] == "华东重点商机"
    assert docs[0].meta["session_name"] == "华东大区W11复盘"


def test_review_snapshot_document_metadata_includes_owner_department_fields():
    snapshot = SimpleNamespace(
        unique_id="snap_meta_2",
        opportunity_id="opp_meta_2",
        opportunity_name="华南重点商机",
        account_id="acc_2",
        account_name="重点客户B",
        owner_id="owner_2",
        owner_name="李四",
        owner_department_id="dept_2",
        owner_department_name="华南销售部",
        snapshot_period="2026-W12",
        forecast_type="upside",
        opportunity_stage="方案",
        expected_closing_date="2026-05-10",
        baseline_forecast_type=None,
        baseline_forecast_amount=None,
        baseline_opportunity_stage=None,
        baseline_expected_closing_date=None,
        ai_commit=None,
        ai_stage=None,
        ai_expected_closing_date=None,
        forecast_amount=None,
        stage_stay=None,
        was_modified=False,
        modification_count=0,
    )
    fake_db = _FakeSnapshotDBSession([snapshot])
    ds = ReviewDataSource(
        db_session=fake_db,
        knowledge_base_id=1,
        data_source_id=1,
        user_id=1,
        review_session_id="rs_meta_2",
    )
    ds._get_session_attendee_owner_ids = lambda _session_id: ["owner_2"]
    session_obj = SimpleNamespace(unique_id="rs_meta_2", period="2026-W12", session_name="W12复盘会")

    docs = list(ds._load_snapshot_documents(session_obj))
    assert len(docs) == 1
    assert docs[0].meta["owner_department_id"] == "dept_2"
    assert docs[0].meta["owner_department_name"] == "华南销售部"
    assert docs[0].meta["session_name"] == "W12复盘会"

    builder = CRMKnowledgeGraphBuilder()
    entities, rels = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_SNAPSHOT,
        primary_data=dict(docs[0].meta),
        secondary_data={"session_id": "rs_meta_2", "session_name": "W12复盘"},
        document_id="doc_meta_2",
        chunk_id="chunk_meta_2",
        meta={"crm_data_type": CrmDataType.REVIEW_SNAPSHOT, "session_id": "rs_meta_2"},
        chunk_text="",
    )
    owner_entity = next(e for e in entities if e["name"] == "李四")
    assert owner_entity["meta"]["internal_owner_id"] == "owner_2"
    assert owner_entity["meta"]["internal_department"] == "华南销售部"
    assert "主属部门华南销售部" in owner_entity["description"]

    handled_by_rel = next(
        r for r in rels if r.get("meta", {}).get("relation_type") == "HANDLED_BY"
    )
    assert handled_by_rel["meta"]["owner_id"] == "owner_2"
    assert handled_by_rel["meta"]["owner_department_id"] == "dept_2"
    assert handled_by_rel["meta"]["owner_department_name"] == "华南销售部"

    snapshot_belongs_to_session = next(
        r for r in rels if r.get("meta", {}).get("relation_type") == "BELONGS_TO"
    )
    assert snapshot_belongs_to_session["target_entity"] == "W12复盘"


def test_review_risk_progress_metadata_includes_type_and_opportunity_name():
    rp = SimpleNamespace(
        unique_id="rp_meta_1",
        record_type="RISK",
        type_name="阶段停滞",
        type_code="stage_stall",
        scope_type="opportunity",
        scope_id="opp_3",
        snapshot_period="2026-W12",
        calc_phase="first_calc_ready",
        severity="HIGH",
        opportunity_id="opp_3",
        owner_id="owner_3",
        department_id="dept_3",
        # fields used by formatter
        category=None,
        ai_assessment=None,
        sales_assessment=None,
        judgment_rule=None,
        summary=None,
        gap_description=None,
        detail_description=None,
        solution=None,
        evidence=None,
        financial_impact=None,
        previous_value=None,
        current_value=None,
        rate_of_change=None,
        status=None,
        detected_at=None,
        resolved_at=None,
        resolved_by=None,
        resolution_type=None,
        resolution_note=None,
        created_at=None,
        updated_at=None,
        created_by=None,
        updated_by=None,
        metadata_={"debug": "noise"},
    )
    fake_db = _FakeRiskProgressDBSession([rp])
    ds = ReviewDataSource(
        db_session=fake_db,
        knowledge_base_id=1,
        data_source_id=1,
        user_id=1,
        review_session_id="rs_meta_rp",
    )
    ds._build_scope_name_maps = lambda _session_obj: {
        "opportunity": {"opp_3": "华北大单"},
        "owner": {"owner_3": "王五"},
        "department": {"dept_3": "华北销售部"},
    }
    session_obj = SimpleNamespace(unique_id="rs_meta_rp", session_name="W12复盘会")

    docs = list(ds._load_risk_progress_documents(session_obj))
    assert len(docs) == 1
    assert docs[0].meta["type_name"] == "阶段停滞"
    assert docs[0].meta["severity"] == "HIGH"
    assert docs[0].meta["opportunity_id"] == "opp_3"
    assert docs[0].meta["opportunity_name"] == "华北大单"
    assert docs[0].meta["session_name"] == "W12复盘会"
    assert "## KG/Vector Facts" in docs[0].content
    assert "- **计算阶段**:" not in docs[0].content
    assert "计算阶段=" not in docs[0].content
    assert "## 扩展元数据" not in docs[0].content
    assert "- [事实] 记录性质=风险；类型=阶段停滞；范围类型=商机" in docs[0].content
    assert "- [事实] 严重程度=HIGH" in docs[0].content
    assert "## 依据数据" not in docs[0].content
    assert "## 记录审计" not in docs[0].content


def test_review_risk_progress_fact_line_is_scope_aware_for_company_scope():
    rp = SimpleNamespace(
        unique_id="rp_meta_company_1",
        record_type="PROGRESS",
        type_name="整体推进",
        type_code="overall_progress",
        scope_type="company",
        scope_id="",
        snapshot_period="2026-W12",
        calc_phase="first_calc_ready",
        severity=None,
        opportunity_id=None,
        owner_id=None,
        department_id=None,
        # fields used by formatter
        category=None,
        ai_assessment=None,
        sales_assessment=None,
        judgment_rule=None,
        summary=None,
        gap_description=None,
        detail_description=None,
        solution=None,
        evidence=None,
        financial_impact=None,
        previous_value=None,
        current_value=None,
        rate_of_change=None,
        status=None,
        detected_at=None,
        resolved_at=None,
        resolved_by=None,
        resolution_type=None,
        resolution_note=None,
        created_at=None,
        updated_at=None,
        created_by=None,
        updated_by=None,
        metadata_=None,
    )
    fake_db = _FakeRiskProgressDBSession([rp])
    ds = ReviewDataSource(
        db_session=fake_db,
        knowledge_base_id=1,
        data_source_id=1,
        user_id=1,
        review_session_id="rs_meta_company",
    )
    ds._build_scope_name_maps = lambda _session_obj: {"opportunity": {}, "owner": {}, "department": {}}
    session_obj = SimpleNamespace(unique_id="rs_meta_company")

    docs = list(ds._load_risk_progress_documents(session_obj))
    assert len(docs) == 1
    assert "公司范围=全公司" in docs[0].content
    assert "关联商机=缺失" not in docs[0].content
    assert "负责人=缺失" not in docs[0].content


def test_review_risk_progress_relations_include_session_name_and_opportunity_id():
    builder = CRMKnowledgeGraphBuilder()
    primary_data = {
        "unique_id": "rp_rel_1",
        "session_id": "rs_rel_1",
        "snapshot_period": "2026-W12",
        "calc_phase": "first_calc_ready",
        "record_type": "RISK",
        "type_name": "阶段停滞",
        "type_code": "stage_stall",
        "scope_type": "opportunity",
        "scope_id": "opp_3",
        "opportunity_id": "opp_3",
        "opportunity_name": "华北大单",
    }
    entities, rels = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_RISK_PROGRESS,
        primary_data=primary_data,
        secondary_data={"session_id": "rs_rel_1", "session_name": "W12复盘会", "period": "2026-W12"},
        document_id="doc_rp_1",
        chunk_id="chunk_rp_1",
        meta={"crm_data_type": CrmDataType.REVIEW_RISK_PROGRESS, "session_id": "rs_rel_1"},
        chunk_text="",
    )

    belongs_to_session = next(
        r for r in rels if r.get("meta", {}).get("relation_type") == "BELONGS_TO"
    )
    assert belongs_to_session["target_entity"] == "W12复盘会"
    assert belongs_to_session["meta"]["relation_subtype"] == "BELONGS_TO_SESSION"

    detected_in_opp = next(
        r for r in rels if r.get("meta", {}).get("relation_type") == "DETECTED_IN"
    )
    assert detected_in_opp["meta"]["opportunity_id"] == "opp_3"
    assert detected_in_opp["target_entity"] == "华北大单"

    rp_entity = next(e for e in entities if e["meta"].get("type_code") == "stage_stall")
    assert rp_entity["name"] == "阶段停滞_2026-W12"


def test_review_risk_progress_department_scope_adds_department_relation():
    builder = CRMKnowledgeGraphBuilder()
    primary_data = {
        "unique_id": "rp_rel_dept_1",
        "session_id": "rs_rel_dept_1",
        "snapshot_period": "2026-W12",
        "calc_phase": "first_calc_ready",
        "record_type": "RISK",
        "type_name": "覆盖不足",
        "type_code": "dept_gap",
        "scope_type": "department",
        "scope_id": "dept_42",
        "scope_name": "华北销售部",
    }
    _, rels = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_RISK_PROGRESS,
        primary_data=primary_data,
        secondary_data={"session_id": "rs_rel_dept_1", "session_name": "W12复盘会"},
        document_id="doc_rp_dept_1",
        chunk_id="chunk_rp_dept_1",
        meta={"crm_data_type": CrmDataType.REVIEW_RISK_PROGRESS, "session_id": "rs_rel_dept_1"},
        chunk_text="",
    )
    affects_dept = next(
        r for r in rels if r.get("meta", {}).get("relation_type") == "AFFECTS_DEPARTMENT"
    )
    assert affects_dept["target_entity"] == "华北销售部"
    assert affects_dept["meta"]["department_id"] == "dept_42"
    assert affects_dept["meta"]["department_name"] == "华北销售部"


def test_review_session_metadata_includes_session_name_and_builder_uses_it():
    ds = ReviewDataSource(
        db_session=_FakeNoopDBSession(),
        knowledge_base_id=1,
        data_source_id=1,
        user_id=1,
        review_session_id="rs_meta_session",
    )
    ds._get_kpi_metrics = lambda _session_obj: []
    session_obj = SimpleNamespace(
        unique_id="rs_meta_session",
        session_name="华东大区W15复盘",
        department_id="dept_s_1",
        department_name="银行二部",
        period="2026-W15",
        stage="first_calc_ready",
    )

    docs = list(ds._load_session_document(session_obj))
    assert len(docs) == 1
    assert docs[0].meta["session_name"] == "华东大区W15复盘"

    builder = CRMKnowledgeGraphBuilder()
    entities, _ = builder.build_graph_from_document_data(
        crm_data_type=CrmDataType.REVIEW_SESSION,
        primary_data=dict(docs[0].meta),
        secondary_data={},
        document_id="doc_session_1",
        chunk_id="chunk_session_1",
        meta={"crm_data_type": CrmDataType.REVIEW_SESSION, "session_id": "rs_meta_session"},
        chunk_text="",
    )
    session_entity = next(e for e in entities if e["meta"].get("session_id") == "rs_meta_session")
    assert session_entity["name"] == "华东大区W15复盘"


def test_review_session_formatter_excludes_period_type_and_stage_fact_dimension():
    session_obj = SimpleNamespace(
        session_name="华东大区W15复盘",
        department_name="银行二部",
        period="2026-W15",
        period_type="WEEKLY",
        period_start="2026-04-06",
        period_end="2026-04-12",
        stage="first_calc_ready",
        review_type="regular",
        fiscal_year=2026,
        report_date="2026-04-13",
    )
    kpi_metrics = [
        SimpleNamespace(
            scope_type="department",
            scope_name="银行二部",
            metric_name="target",
            metric_value=100,
            metric_value_prev=90,
            metric_delta=10,
            metric_rate=0.111,
        )
    ]
    lines = format_review_session_info(session_obj, kpi_metrics=kpi_metrics)
    content = "\n".join(lines)
    assert "- **周期类型**:" not in content
    assert "事实维度：周期=2026-W15；范围分组=部门范围" in content
    assert "阶段=first_calc_ready" not in content


def test_review_session_formatter_omits_missing_kpi_fact_lines():
    session_obj = SimpleNamespace(
        session_name="华东大区W15复盘",
        department_name="银行二部",
        period="2026-W15",
        stage="first_calc_ready",
    )
    kpi_metrics = [
        SimpleNamespace(
            scope_type="department",
            scope_name="银行二部",
            metric_name="target",
            metric_value=100,
            metric_value_prev=90,
            metric_delta=10,
            metric_rate=0.111,
        )
    ]
    lines = format_review_session_info(session_obj, kpi_metrics=kpi_metrics)
    content = "\n".join(lines)
    assert "指标=目标 (target)" in content
    assert "当前值=缺失" not in content


def test_snapshot_formatter_excludes_baseline_source_and_change_tracking():
    snapshot = SimpleNamespace(
        opportunity_name="华北大单",
        account_name="甲方集团",
        owner_name="王五",
        owner_department_name="华北销售部",
        snapshot_period="2026-W15",
        baseline_forecast_type="COMMIT",
        baseline_forecast_amount=1000000,
        baseline_opportunity_stage="谈判",
        baseline_expected_closing_date="2026-05-01",
        forecast_type="UPSIDE",
        forecast_amount=1200000,
        opportunity_stage="方案",
        expected_closing_date="2026-05-15",
        stage_stay=12,
        ai_commit="COMMIT",
        ai_stage="谈判",
        ai_expected_closing_date="2026-05-01",
        was_modified=True,
        modification_count=3,
    )
    lines = format_snapshot_info(snapshot)
    content = "\n".join(lines)
    assert "基线来源=" not in content
    assert "## 变更状态" not in content
    assert "修改次数=" not in content
