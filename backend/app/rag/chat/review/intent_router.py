import logging
import re
from typing import List, Literal, Optional

from llama_index.core.llms.llm import LLM
from llama_index.core.prompts.rich import RichPromptTemplate
from pydantic import BaseModel, Field

from app.rag.chat.review.prompts import REVIEW_INTENT_CLASSIFICATION_PROMPT

logger = logging.getLogger(__name__)


class ReviewIntent(BaseModel):
    intent_type: Literal["data_query", "root_cause", "strategy"] = Field(
        description="Question intent: data_query (what), root_cause (why), strategy (how)"
    )
    metric_names: List[str] = Field(
        default_factory=list,
        description="Relevant metric names, e.g. commit_amount, pipeline_count",
    )
    scope_type: Optional[str] = Field(
        default=None,
        description="Scope: company / department / owner / opportunity",
    )
    scope_id: Optional[str] = Field(
        default=None,
        description="Specific scope ID (department_id / owner_id, etc.)",
    )
    time_comparison: Optional[str] = Field(
        default="current_only",
        description="wow (week-over-week) / mom (month-over-month) / current_only",
    )
    opportunity_id: Optional[str] = Field(
        default=None,
        description="Specific opportunity ID if question is about a single opportunity",
    )
    opportunity_name_keyword: Optional[str] = Field(
        default=None,
        description=(
            "Substring of opportunity_name or account_name when the user refers to "
            "an opportunity or customer by name (e.g. 马上消费) without CRM ID"
        ),
    )
    needs_clarification: bool = Field(
        default=False,
        description="Whether the parser is uncertain and should ask a follow-up question.",
    )
    clarifying_question: str = Field(
        default="",
        description="A short follow-up question when needs_clarification is true.",
    )


class ReviewSessionContext(BaseModel):
    """Metadata about the current review session, injected into the intent prompt."""

    session_id: str
    department_name: Optional[str] = None
    period: str = ""
    period_type: str = ""
    period_start: str = ""
    period_end: str = ""
    stage: str = ""
    review_phase: Optional[str] = None


class ReviewIntentRouter:
    """Uses fast LLM to classify a user question into one of three intent types
    and extract structured parameters for downstream data retrieval."""

    def __init__(self, fast_llm: LLM):
        self._fast_llm = fast_llm

    def classify(
        self,
        user_question: str,
        session_context: ReviewSessionContext,
        chat_history: Optional[List] = None,
    ) -> ReviewIntent:
        prompt = RichPromptTemplate(REVIEW_INTENT_CLASSIFICATION_PROMPT)
        raw = self._fast_llm.predict(
            prompt,
            user_question=user_question,
            session_id=session_context.session_id,
            department_name=session_context.department_name or "",
            period=session_context.period,
            period_type=session_context.period_type,
            period_start=session_context.period_start,
            period_end=session_context.period_end,
            stage=session_context.stage,
            review_phase=session_context.review_phase or "",
            chat_history=chat_history or [],
        )
        intent = self._parse_intent(raw)
        return self._apply_soft_boundary_guard(intent, user_question)

    @staticmethod
    def _parse_intent(raw: str) -> ReviewIntent:
        try:
            cleaned = ReviewIntentRouter._extract_json(raw)
            return ReviewIntent.model_validate_json(cleaned)
        except Exception as e:
            logger.warning("Failed to parse intent JSON (%s), falling back to data_query: %s", e, raw[:200])
            return ReviewIntent(intent_type="data_query")

    @staticmethod
    def _extract_json(text: str) -> str:
        code_block = re.search(r"```(?:json)?\n([\s\S]*?)\n```", text)
        if code_block:
            return code_block.group(1).strip()
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json_match.group(0).strip()
        return text.strip()

    @staticmethod
    def _apply_soft_boundary_guard(intent: ReviewIntent, user_question: str) -> ReviewIntent:
        """Apply lightweight boundary rules for precision-first data_query."""
        if intent.needs_clarification:
            return intent
        if intent.intent_type != "data_query":
            return intent
        q = (user_question or "").strip().lower()
        if not q:
            return intent

        # Soft boundary: for "sales vs AI mismatch list" without explicit dimension,
        # ask one clarification instead of defaulting to a potentially wrong mapping.
        has_sales = ("销售" in q) or ("业务" in q)
        has_ai = ("ai" in q) or ("智能" in q) or ("算法" in q)
        has_diff = any(k in q for k in ("不同", "不一致", "差异", "不一样", "冲突"))
        has_list = any(k in q for k in ("哪些", "列表", "清单", "列出"))
        has_opportunity = any(k in q for k in ("商机", "项目", "pipeline", "机会"))
        has_explicit_dimension = any(
            k in q
            for k in (
                "阶段",
                "stage",
                "预测",
                "forecast",
                "预测状态",
                "预计成交",
                "成交日期",
                "close date",
                "closing date",
            )
        )
        if has_sales and has_ai and has_diff and has_list and has_opportunity and not has_explicit_dimension:
            intent.needs_clarification = True
            intent.clarifying_question = (
                "你想看哪一类差异：商机阶段、预测状态，还是预计成交日期？"
            )
            return intent

        return intent
