from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from llama_index.core.prompts.rich import RichPromptTemplate
from llama_index.core.schema import NodeWithScore
from sqlmodel import Session, select

from app.models.crm_review import CRMReviewSession
from app.rag.chat.review import prompts as review_prompts
from app.rag.chat.review.context_builder import ReviewContextBuilder
from app.rag.chat.review.data_retriever import ReviewDataRetriever
from app.rag.chat.review.intent_router import ReviewIntentRouter, ReviewSessionContext
from app.rag.chat.review.workflow_schema import (
    AgentTask,
    RecommendationItem,
    WorkflowArtifacts,
    WorkflowPlan,
)
from app.rag.types import ChatEventType, ChatMessageSate, CrmDataType
from app.rag.chat.stream_protocol import ChatEvent, ChatStreamMessagePayload

logger = logging.getLogger(__name__)


class ReviewPlannerAgent:
    def __init__(self, fast_llm):
        self.intent_router = ReviewIntentRouter(fast_llm=fast_llm)

    def plan(
        self,
        user_question: str,
        session_context: ReviewSessionContext,
        chat_history: Optional[List] = None,
    ) -> Tuple[WorkflowPlan, Any]:
        intent = self.intent_router.classify(
            user_question=user_question,
            session_context=session_context,
            chat_history=chat_history or [],
        )
        tasks = [
            AgentTask(task_id="t1", task_type="intent_classification"),
            AgentTask(task_id="t2", task_type="structured_data_retrieval", depends_on=["t1"]),
            AgentTask(task_id="t5", task_type="reasoning", depends_on=["t2"]),
            AgentTask(task_id="t6", task_type="response_generation", depends_on=["t5"]),
        ]
        if intent.intent_type == "strategy":
            tasks.insert(2, AgentTask(task_id="t3", task_type="kg_retrieval", depends_on=["t1"]))
            tasks.insert(3, AgentTask(task_id="t4", task_type="vector_retrieval", depends_on=["t1"]))

        workflow_id = hashlib.md5(
            f"{session_context.session_id}-{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()[:12]
        plan = WorkflowPlan(
            workflow_id=workflow_id,
            session_id=session_context.session_id,
            intent_type=intent.intent_type,
            tasks=tasks,
            metadata={"time_comparison": intent.time_comparison},
        )
        return plan, intent


class ReviewRecommendationAgent:
    @staticmethod
    def build_recommendations(
        session_ctx: ReviewSessionContext,
        user_question: str,
        response_text: str,
        top_chunks: List[NodeWithScore],
    ) -> List[RecommendationItem]:
        if not response_text.strip():
            return []
        lines = [l.strip("- ").strip() for l in response_text.splitlines() if l.strip().startswith(("-", "1.", "2.", "3."))]
        lines = [l for l in lines if len(l) >= 8][:5]
        items: List[RecommendationItem] = []
        for idx, line in enumerate(lines, 1):
            rec_id = hashlib.md5(
                f"{session_ctx.session_id}-{session_ctx.period}-{line}-{idx}".encode()
            ).hexdigest()[:16]
            refs = []
            if idx - 1 < len(top_chunks):
                meta = top_chunks[idx - 1].node.metadata or {}
                refs.append(str(meta.get("document_id") or meta.get("source_uri") or "chunk"))
            items.append(
                RecommendationItem(
                    recommendation_id=rec_id,
                    title=f"建议{idx}",
                    action=line,
                    rationale="基于当前review结构化数据与知识库经验生成",
                    evidence_refs=refs,
                    expected_metric="commit_sales / pipeline_coverage",
                    validation_checkpoint=f"下周({session_ctx.period})复盘该建议执行结果",
                    score=max(0.0, 1 - idx * 0.1),
                )
            )
        return items


class AgentWorkFlow:
    def __init__(
        self,
        *,
        db_session: Session,
        llm,
        fast_llm,
        retrieve_flow,
        user_question: str,
        chat_history: List,
        session_ctx: ReviewSessionContext,
        review_session: CRMReviewSession,
        search_knowledge_graph: Callable[..., Generator],
    ):
        self.db_session = db_session
        self.llm = llm
        self.fast_llm = fast_llm
        self.retrieve_flow = retrieve_flow
        self.user_question = user_question
        self.chat_history = chat_history
        self.session_ctx = session_ctx
        self.review_session = review_session
        self.search_knowledge_graph = search_knowledge_graph
        self.planner = ReviewPlannerAgent(fast_llm=fast_llm)
        self.data_retriever = ReviewDataRetriever()

    @staticmethod
    def _review_type_values() -> set[str]:
        return {
            CrmDataType.REVIEW_SESSION.value,
            CrmDataType.REVIEW_SNAPSHOT.value,
            CrmDataType.REVIEW_RISK_PROGRESS.value,
            "crm_review_recommendation",
        }

    def _build_time_metadata(self) -> Dict[str, Any]:
        period_start = self.review_session.period_start
        period_end = self.review_session.period_end
        week_id = self.review_session.period
        rank_stmt = (
            select(CRMReviewSession.id)
            .where(CRMReviewSession.department_id == self.review_session.department_id)
            .where(CRMReviewSession.period_start <= period_start)
        )
        session_week_rank = len(list(self.db_session.exec(rank_stmt).all()))
        return {
            "time_granularity": "week",
            "week_id": week_id,
            "week_start": str(period_start),
            "week_end": str(period_end),
            "session_week_rank": session_week_rank,
        }

    def _filter_strategy_chunks(
        self, chunks: List[NodeWithScore], week_id: str, session_id: str
    ) -> List[NodeWithScore]:
        review_types = self._review_type_values()
        filtered = []
        for chunk in chunks:
            meta = chunk.node.metadata or {}
            chunk_type = str(meta.get("crm_data_type", ""))
            if chunk_type and chunk_type not in review_types:
                continue
            chunk_period = str(meta.get("snapshot_period", "") or meta.get("week_id", ""))
            chunk_session = str(meta.get("session_id", "") or "")
            if chunk_period and chunk_period != week_id:
                continue
            if chunk_session and chunk_session != session_id:
                continue
            filtered.append(chunk)
        return filtered

    def _rerank_with_feedback(self, chunks: List[NodeWithScore]) -> List[NodeWithScore]:
        def weight(chunk: NodeWithScore) -> float:
            meta = chunk.node.metadata or {}
            base = float(chunk.score or 0.0)
            outcome = str(meta.get("recommendation_outcome", "")).lower()
            feedback = {"improved": 0.2, "no_change": 0.0, "worse": -0.15}.get(outcome, 0.0)
            recency = 0.0
            if meta.get("week_id") == self.session_ctx.period:
                recency = 0.1
            return base + feedback + recency

        return sorted(chunks, key=weight, reverse=True)

    def execute(self) -> Generator[ChatEvent, None, WorkflowArtifacts]:
        yield ChatEvent(
            event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
            payload=ChatStreamMessagePayload(
                state=ChatMessageSate.REVIEW_INTENT_CLASSIFICATION,
                display="Planner is building multi-agent execution plan",
            ),
        )
        plan, intent = self.planner.plan(
            user_question=self.user_question,
            session_context=self.session_ctx,
            chat_history=self.chat_history,
        )
        artifacts = WorkflowArtifacts(session_context=self.session_ctx, intent=intent)
        artifacts.metadata["workflow_plan"] = plan.model_dump()
        artifacts.metadata["time_axis"] = self._build_time_metadata()

        if getattr(intent, "needs_clarification", False):
            clarification = str(getattr(intent, "clarifying_question", "") or "").strip()
            artifacts.response_text = clarification or "请先明确问题范围，我再继续分析。"
            return artifacts

        yield ChatEvent(
            event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
            payload=ChatStreamMessagePayload(
                state=ChatMessageSate.REVIEW_DATA_RETRIEVAL,
                display="Structured-data agent is retrieving review metrics",
            ),
        )
        artifacts.data_context = self.data_retriever.retrieve(
            db_session=self.db_session,
            review_session=self.review_session,
            intent=intent,
            user_question=self.user_question,
        )
        artifacts.structured_context = ReviewContextBuilder.build_structured_context(intent, artifacts.data_context)
        artifacts.risk_context = ReviewContextBuilder.build_risk_context(intent, artifacts.data_context)

        if intent.intent_type == "strategy":
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.REVIEW_CONTEXT_BUILDING,
                    display="KG/Vector agents are retrieving time-scoped best practices",
                ),
            )
            knowledge_graph_result, kg_ctx = yield from self.search_knowledge_graph(
                user_question=self.user_question,
                annotation_silent=True,
            )
            review_types = self._review_type_values()
            relationships = []
            for rel in knowledge_graph_result.relationships:
                meta = rel.meta or {}
                rel_type = str(meta.get("crm_data_type", ""))
                if rel_type and rel_type not in review_types:
                    continue
                rel_period = str(meta.get("snapshot_period", "") or meta.get("week_id", ""))
                rel_session = str(meta.get("session_id", "") or "")
                if rel_period and rel_period != self.session_ctx.period:
                    continue
                if rel_session and rel_session != self.session_ctx.session_id:
                    continue
                relationships.append(rel)
            if relationships:
                entity_ids = set()
                for rel in relationships:
                    entity_ids.add(rel.source_entity_id)
                    entity_ids.add(rel.target_entity_id)
                knowledge_graph_result.relationships = relationships
                knowledge_graph_result.entities = [e for e in knowledge_graph_result.entities if e.id in entity_ids]
                kg_ctx = self.retrieve_flow._get_knowledge_graph_context(knowledge_graph_result)
            else:
                kg_ctx = ""
            artifacts.knowledge_graph_context = kg_ctx

            chunks = self.retrieve_flow.search_relevant_chunks(
                self.user_question,
                crm_authority=None,
                granted_files=[],
            )
            chunks = self._filter_strategy_chunks(
                chunks, week_id=self.session_ctx.period, session_id=self.session_ctx.session_id
            )
            artifacts.relevant_chunks = self._rerank_with_feedback(chunks)
            artifacts.kb_context = ReviewContextBuilder.build_kb_context(
                artifacts.knowledge_graph_context,
                artifacts.relevant_chunks,
            )

        yield ChatEvent(
            event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
            payload=ChatStreamMessagePayload(
                state=ChatMessageSate.GENERATE_ANSWER,
                display="Reasoning/response agents are generating final answer",
            ),
        )
        prompt_map = {
            "data_query": review_prompts.REVIEW_DATA_QUERY_PROMPT,
            "root_cause": review_prompts.REVIEW_ROOT_CAUSE_PROMPT,
            "strategy": review_prompts.REVIEW_STRATEGY_PROMPT,
        }
        prompt_template = RichPromptTemplate(prompt_map[intent.intent_type])
        format_kwargs = {
            "current_date": datetime.now().strftime("%Y-%m-%d"),
            "period": self.session_ctx.period,
            "period_start": self.session_ctx.period_start,
            "period_end": self.session_ctx.period_end,
            "department_name": self.session_ctx.department_name or "",
            "structured_context": artifacts.structured_context,
            "user_question": self.user_question,
        }
        if intent.intent_type in ("root_cause", "strategy"):
            format_kwargs["risk_context"] = artifacts.risk_context
        if intent.intent_type == "strategy":
            format_kwargs["kb_context"] = artifacts.kb_context

        artifacts.response_text = self.llm.predict(prompt_template, **format_kwargs) or "未能生成回答，请稍后重试。"
        if intent.intent_type == "strategy":
            artifacts.recommendations = ReviewRecommendationAgent.build_recommendations(
                session_ctx=self.session_ctx,
                user_question=self.user_question,
                response_text=artifacts.response_text,
                top_chunks=artifacts.relevant_chunks,
            )
            artifacts.metadata["recommendations"] = [r.model_dump() for r in artifacts.recommendations]
        artifacts.metadata["time_axis"]["cross_session_enabled"] = intent.intent_type in ("root_cause", "strategy")
        artifacts.metadata["workflow_execution_at"] = datetime.utcnow().isoformat()
        artifacts.metadata["workflow_execution_summary"] = json.dumps(
            {
                "intent_type": intent.intent_type,
                "task_count": len(plan.tasks),
                "recommendation_count": len(artifacts.recommendations),
            },
            ensure_ascii=False,
        )
        return artifacts

