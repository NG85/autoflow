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
