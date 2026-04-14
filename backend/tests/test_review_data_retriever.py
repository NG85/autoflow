from types import SimpleNamespace

from app.rag.chat.review.data_retriever import (
    ReviewDataRetriever,
    _detect_mismatch_query_type,
)
from app.rag.chat.review.intent_router import ReviewIntent


def _review_session_stub() -> SimpleNamespace:
    return SimpleNamespace(
        unique_id="session-1",
        period="2026-W11",
        department_id="dept-1",
    )


def _build_intent(**kwargs) -> ReviewIntent:
    payload = {
        "intent_type": "data_query",
        "metric_names": [],
        "scope_type": "department",
        "scope_id": None,
        "time_comparison": "current_only",
        "opportunity_id": None,
        "opportunity_name_keyword": None,
    }
    payload.update(kwargs)
    return ReviewIntent(**payload)


def test_detect_mismatch_query_type_supports_non_list_phrases():
    q = "有没有AI和销售判断冲突的商机"
    assert _detect_mismatch_query_type(q) == "forecast"


def test_detect_mismatch_query_type_fallback_to_stage():
    q = "AI和销售判断不一致的商机有多少"
    assert _detect_mismatch_query_type(q) == "stage"


def test_retrieve_uses_mismatch_branch_with_intent_prior(monkeypatch):
    retriever = ReviewDataRetriever()
    called = {"mismatch": False, "kpi": False}

    def fake_mismatch(*args, **kwargs):
        called["mismatch"] = True
        return []

    def fake_kpi(*args, **kwargs):
        called["kpi"] = True
        return []

    monkeypatch.setattr(retriever, "_query_field_mismatch_opportunities", fake_mismatch)
    monkeypatch.setattr(retriever, "_query_kpi_metrics", fake_kpi)
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    intent = _build_intent(metric_names=["commit_sales", "commit_ai"])
    retriever.retrieve(
        db_session=SimpleNamespace(),
        review_session=_review_session_stub(),
        intent=intent,
        user_question="本周销售和AI判断口径差异情况",
    )

    assert called["mismatch"] is True
    assert called["kpi"] is False


def test_retrieve_mismatch_count_note(monkeypatch):
    retriever = ReviewDataRetriever()
    monkeypatch.setattr(
        retriever,
        "_query_field_mismatch_opportunities",
        lambda *a, **k: [{"opportunity_id": "opp-1"}],
    )
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    ctx = retriever.retrieve(
        db_session=SimpleNamespace(),
        review_session=_review_session_stub(),
        intent=_build_intent(),
        user_question="AI和销售商机阶段不同的商机有多少个？",
    )
    assert "差异计数结果" in (ctx.query_note or "")
    assert "1" in (ctx.query_note or "")


def test_retrieve_general_list_fallback_to_period_rows(monkeypatch):
    retriever = ReviewDataRetriever()
    called = {"period_rows": False}

    monkeypatch.setattr(retriever, "_query_kpi_metrics", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_collect_opportunity_snapshot_rows", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_snapshot_aggregations", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "_query_risk_progress", lambda *a, **k: [])

    def fake_period_rows(*args, **kwargs):
        called["period_rows"] = True
        return [{"opportunity_id": "opp-1", "opportunity_name": "A"}]

    monkeypatch.setattr(retriever, "_query_period_opportunity_rows", fake_period_rows)

    ctx = retriever.retrieve(
        db_session=SimpleNamespace(),
        review_session=_review_session_stub(),
        intent=_build_intent(),
        user_question="本周有哪些商机，给我看列表",
    )

    assert called["period_rows"] is True
    assert len(ctx.opportunity_snapshot_rows) == 1
