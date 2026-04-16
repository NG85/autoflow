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
from app.rag.chat.review.indexing_policy import (
    build_review_datasource_name,
    get_or_create_review_datasource_id,
    normalize_review_data_types,
    validate_review_index_scope_by_stage,
)
from app.rag.indices.knowledge_graph.crm.builder import CRMKnowledgeGraphBuilder
from app.rag.types import CrmDataType


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
    assert "Suggested action" in text
    assert "Detail" in text
    assert "Next step" in text


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


def test_normalize_review_data_types_defaults_to_snapshot_and_risk():
    assert normalize_review_data_types(None) == [
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
