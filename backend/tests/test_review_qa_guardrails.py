import pytest

pytest.importorskip("llama_index")

from app.rag.chat.review.data_retriever import (
    ReviewDataContext,
    _detect_mismatch_query_type,
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
