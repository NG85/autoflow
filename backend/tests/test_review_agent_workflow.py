from types import SimpleNamespace

from app.rag.chat.review.agent_workflow import AgentWorkFlow
from app.rag.chat.review.intent_router import ReviewIntent, ReviewSessionContext
from app.rag.chat.review.workflow_schema import AgentTask, WorkflowPlan
from app.rag.chat.review.data_retriever import ReviewDataContext
from app.rag.types import ChatEventType


class _FakeNode:
    def __init__(self, metadata=None, content="chunk content"):
        self.metadata = metadata or {}
        self._content = content

    def get_content(self):
        return self._content


class _FakeChunk:
    def __init__(self, score=0.8, metadata=None, content="chunk content"):
        self.score = score
        self.node = _FakeNode(metadata=metadata, content=content)


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text

    def predict(self, *_args, **_kwargs):
        return self._text


class _FakeRetrieveFlow:
    def search_relevant_chunks(self, *_args, **_kwargs):
        return [
            _FakeChunk(
                metadata={
                    "crm_data_type": "crm_review_recommendation",
                    "week_id": "2026-W11",
                    "session_id": "session-1",
                    "recommendation_outcome": "improved",
                    "document_id": 123,
                }
            )
        ]

    def _get_knowledge_graph_context(self, _kg_result):
        return "kg context"


def _fake_search_kg(**_kwargs):
    kg = SimpleNamespace(relationships=[], entities=[])
    if False:
        yield None
    return kg, ""


def test_agent_workflow_returns_clarification_early(monkeypatch):
    workflow = AgentWorkFlow(
        db_session=SimpleNamespace(),
        llm=_FakeLLM("ignored"),
        fast_llm=_FakeLLM("ignored"),
        retrieve_flow=_FakeRetrieveFlow(),
        user_question="这个问题不明确",
        chat_history=[],
        session_ctx=ReviewSessionContext(session_id="session-1", period="2026-W11"),
        review_session=SimpleNamespace(
            period="2026-W11",
            period_start="2026-03-17",
            period_end="2026-03-23",
            department_id="dept-1",
        ),
        search_knowledge_graph=_fake_search_kg,
    )

    intent = ReviewIntent(
        intent_type="data_query",
        needs_clarification=True,
        clarifying_question="请明确口径",
    )
    plan = WorkflowPlan(
        workflow_id="wf-1",
        session_id="session-1",
        intent_type="data_query",
        tasks=[AgentTask(task_id="t1", task_type="intent_classification")],
    )
    monkeypatch.setattr(workflow.planner, "plan", lambda **_kwargs: (plan, intent))
    monkeypatch.setattr(workflow, "_build_time_metadata", lambda: {"week_id": "2026-W11"})

    events = []
    gen = workflow.execute()
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            result = stop.value
            break

    assert any(e.event_type == ChatEventType.MESSAGE_ANNOTATIONS_PART for e in events)
    assert result.response_text == "请明确口径"


def test_agent_workflow_strategy_generates_recommendations(monkeypatch):
    workflow = AgentWorkFlow(
        db_session=SimpleNamespace(),
        llm=_FakeLLM("- 建议一：聚焦高概率机会\n- 建议二：强化推进节奏"),
        fast_llm=_FakeLLM("ignored"),
        retrieve_flow=_FakeRetrieveFlow(),
        user_question="给我策略建议",
        chat_history=[],
        session_ctx=ReviewSessionContext(
            session_id="session-1",
            period="2026-W11",
            department_name="银行二部",
            period_start="2026-03-17",
            period_end="2026-03-23",
        ),
        review_session=SimpleNamespace(
            period="2026-W11",
            period_start="2026-03-17",
            period_end="2026-03-23",
            department_id="dept-1",
        ),
        search_knowledge_graph=_fake_search_kg,
    )
    plan = WorkflowPlan(
        workflow_id="wf-2",
        session_id="session-1",
        intent_type="strategy",
        tasks=[AgentTask(task_id="t1", task_type="intent_classification")],
    )
    intent = ReviewIntent(intent_type="strategy")

    monkeypatch.setattr(workflow.planner, "plan", lambda **_kwargs: (plan, intent))
    monkeypatch.setattr(workflow, "_build_time_metadata", lambda: {"week_id": "2026-W11"})
    monkeypatch.setattr(
        workflow.data_retriever,
        "retrieve",
        lambda **_kwargs: ReviewDataContext(),
    )

    events = []
    gen = workflow.execute()
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            result = stop.value
            break

    assert any(e.event_type == ChatEventType.MESSAGE_ANNOTATIONS_PART for e in events)
    assert len(result.recommendations) >= 1
    assert "recommendations" in result.metadata
