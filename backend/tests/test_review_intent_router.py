from app.rag.chat.review.intent_router import ReviewIntent, ReviewIntentRouter


def test_soft_boundary_guard_adds_metric_prior_for_ai_sales_diff():
    intent = ReviewIntent(intent_type="data_query", metric_names=[])
    out = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "AI和销售判断不一致的商机有哪些",
    )
    assert set(out.metric_names) == {"commit_sales", "commit_ai"}


def test_soft_boundary_guard_keeps_non_data_query_unchanged():
    intent = ReviewIntent(intent_type="strategy", metric_names=[])
    out = ReviewIntentRouter._apply_soft_boundary_guard(
        intent,
        "AI和销售判断不一致的商机有哪些",
    )
    assert out.intent_type == "strategy"
    assert out.metric_names == []
