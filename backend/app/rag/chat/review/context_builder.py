"""Assembles LLM context from structured data and optional unstructured retrieval results."""

import logging
from typing import Dict, List, Optional

from llama_index.core.schema import NodeWithScore

from app.rag.chat.review.data_retriever import ReviewDataContext
from app.rag.chat.review.intent_router import ReviewIntent
from app.rag.types import CrmDataType

logger = logging.getLogger(__name__)

REVIEW_CRM_DATA_TYPES = {
    CrmDataType.REVIEW_SESSION,
    CrmDataType.REVIEW_SNAPSHOT,
    CrmDataType.REVIEW_RISK_PROGRESS,
}


class ReviewContextBuilder:
    """Builds the final context strings consumed by the QA prompt templates.

    Assembly strategy depends on intent_type:
    - data_query   -> structured data only, compact table/list format
    - root_cause   -> structured data (KPI deltas + risk details), comparison format
    - strategy     -> structured data + KG/vector retrieval results, comprehensive format
    """

    @staticmethod
    def build_structured_context(
        intent: ReviewIntent,
        data_ctx: ReviewDataContext,
    ) -> str:
        return data_ctx.to_context_text()

    @staticmethod
    def build_risk_context(
        intent: ReviewIntent,
        data_ctx: ReviewDataContext,
    ) -> str:
        if intent.intent_type == "data_query":
            return ""
        return data_ctx.to_risk_context_text()

    @staticmethod
    def build_kb_context(
        knowledge_graph_context: str = "",
        relevant_chunks: Optional[List[NodeWithScore]] = None,
    ) -> str:
        """Merge KG context and chunk text for the strategy prompt."""
        parts: List[str] = []

        if knowledge_graph_context:
            parts.append("#### Knowledge Graph\n" + knowledge_graph_context)

        if relevant_chunks:
            parts.append("#### Related Documents")
            for i, chunk in enumerate(relevant_chunks, 1):
                text = chunk.node.get_content()
                score = chunk.score or 0
                parts.append(f"[{i}] (score={score:.2f}) {text[:600]}")

        return "\n\n".join(parts) if parts else "(No knowledge base context available)"

    @staticmethod
    def build_review_kg_filters(
        session_id: Optional[str] = None,
        snapshot_period: Optional[str] = None,
        week_id: Optional[str] = None,
        cross_session: bool = False,
    ) -> Dict:
        """Build Mongo-style metadata filters for KG/vector retrieval scoped to review data.

        Parameters
        ----------
        session_id : str | None
            Restrict to a specific review session.
        snapshot_period : str | None
            Restrict to a specific period (e.g. ``"2026-W14"``).
        cross_session : bool
            If True, omit session/period constraints so that the query
            spans all historical review data (still restricted to review
            ``crm_data_type`` values).

        Returns
        -------
        dict
            Filter dict compatible with ``relationship_meta_filters``.
        """
        crm_type_values = [t.value for t in REVIEW_CRM_DATA_TYPES]
        filters: Dict = {
            "crm_data_type": {"$in": crm_type_values},
        }

        if not cross_session:
            period_value = week_id or snapshot_period
            if period_value:
                filters["snapshot_period"] = {"$eq": period_value}
                filters["week_id"] = {"$eq": period_value}
            elif session_id:
                filters["session_id"] = {"$eq": session_id}

        return filters
