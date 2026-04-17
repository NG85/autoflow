import logging
import re
from typing import Any, Dict, List, Literal, Optional

from llama_index.core.llms.llm import LLM
from llama_index.core.prompts.rich import RichPromptTemplate
from pydantic import BaseModel, Field

from app.rag.chat.review.prompts import REVIEW_INTENT_CLASSIFICATION_PROMPT
from app.rag.chat.review.risk_type_helper import resolve_requested_risk_type_codes

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
    query_type: Optional[
        Literal["kpi_aggregation", "opportunity_detail", "mismatch_list", "risk_progress"]
    ] = Field(
        default=None,
        description="Retrieval route for structured data execution.",
    )
    mismatch_type: Optional[Literal["stage", "forecast", "close_date"]] = Field(
        default=None,
        description="Mismatch dimension when query_type is mismatch_list.",
    )
    detail_filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured detail filters for opportunity_detail route.",
    )
    query_plan: Dict[str, Any] = Field(
        default_factory=dict,
        description="Executable retrieval plan used by downstream retriever.",
    )
    preset_template: Optional[str] = Field(
        default=None,
        description=(
            "Optional frontend preset template identifier. "
            "When provided, backend should execute fixed retrieval logic."
        ),
    )
    template_params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional parameters for preset_template (e.g. risk_type_codes override). "
            "Must stay within backend-supported ranges."
        ),
    )
    intent_confidence: float = Field(
        default=0.0,
        description="Confidence score in [0,1] for intent and route.",
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
    def _resolve_achievement_risk_type_codes(intent: ReviewIntent) -> List[str]:
        """Resolve risk_type_codes from template params with defaults and dedup."""
        return resolve_requested_risk_type_codes(intent.template_params)

    @staticmethod
    def _apply_soft_boundary_guard(intent: ReviewIntent, user_question: str) -> ReviewIntent:
        """Apply lightweight boundary rules for precision-first data_query."""
        if intent.intent_type != "data_query":
            return intent
        # Frontend preset templates should use fixed backend execution logic.
        if intent.preset_template == "achievement_risk_overview":
            risk_codes = ReviewIntentRouter._resolve_achievement_risk_type_codes(intent)
            intent.query_type = "risk_progress"
            intent.query_plan = {
                "template_id": "achievement_risk_overview",
                "route": "risk_progress",
                "risk_type_codes": risk_codes,
                "use_kpi": False,
            }
            intent.needs_clarification = False
            intent.clarifying_question = ""
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
        has_detail_lookup = any(
            k in q
            for k in (
                "明细",
                "列表",
                "清单",
                "列出",
                "查询",
                "查找",
                "哪些",
            )
        )
        has_detail_field = any(
            k in q
            for k in (
                "负责人",
                "owner",
                "阶段",
                "预测",
                "预测状态",
                "成交日期",
                "预计成交",
                "金额",
                "签约金额",
            )
        )
        asks_amount = any(k in q for k in ("金额", "签约金额", "amount", "amt"))
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
        # Domain boundary:
        # - Session-level KPI has AI amount metric (e.g. commit_ai).
        # - Per-opportunity snapshot does NOT have AI amount field.
        # Therefore only clarify when user is asking opportunity-level mismatch list.
        if has_ai and asks_amount and has_diff and has_opportunity and has_list:
            intent.needs_clarification = True
            intent.clarifying_question = (
                "当前单商机快照里没有AI金额字段（但review报告有AI确定下单金额指标）。"
                "你想改为查看哪类单商机差异：商机阶段、预测状态，还是预计成交日期？"
            )
            return intent
        # If the user already gave an explicit mismatch dimension, avoid over-clarifying.
        if has_explicit_dimension and intent.needs_clarification:
            intent.needs_clarification = False
            intent.clarifying_question = ""
            return intent
        if has_sales and has_ai and has_diff and has_list and has_opportunity and not has_explicit_dimension:
            intent.needs_clarification = True
            intent.clarifying_question = (
                "你想看哪一类差异：商机阶段、预测状态，还是预计成交日期？"
            )
            return intent
        # Preset MVP #1: "当前业绩有哪些达成风险？"
        if (
            ("达成风险" in q or "业绩风险" in q)
            and ("当前" in q or "本期" in q or "目前" in q)
        ):
            risk_codes = ReviewIntentRouter._resolve_achievement_risk_type_codes(intent)
            intent.query_type = "risk_progress"
            intent.query_plan = {
                "template_id": "achievement_risk_overview",
                "route": "risk_progress",
                "risk_type_codes": risk_codes,
                "use_kpi": False,
            }
            intent.preset_template = "achievement_risk_overview"
            intent.needs_clarification = False
            intent.clarifying_question = ""
            return intent

        # Route normalization: keep backward compatibility while making retrieval explicit.
        if intent.query_type is None:
            if has_ai and has_diff and (has_list or has_opportunity):
                intent.query_type = "mismatch_list"
            elif has_opportunity and (has_detail_lookup or has_detail_field):
                intent.query_type = "opportunity_detail"
            elif intent.opportunity_id or intent.opportunity_name_keyword:
                intent.query_type = "opportunity_detail"
            else:
                intent.query_type = "kpi_aggregation"
        if intent.query_type == "mismatch_list" and intent.mismatch_type is None:
            if any(k in q for k in ("阶段", "stage")):
                intent.mismatch_type = "stage"
            elif any(k in q for k in ("预测", "forecast", "判断")):
                intent.mismatch_type = "forecast"
            elif any(k in q for k in ("预计成交", "成交日期", "close date", "closing date")):
                intent.mismatch_type = "close_date"
        if not intent.query_plan:
            intent.query_plan = {
                "route": intent.query_type or "kpi_aggregation",
                "mismatch_type": intent.mismatch_type,
                "detail_filters": intent.detail_filters or {},
                "use_kpi": bool((intent.query_type or "kpi_aggregation") == "kpi_aggregation"),
            }
        # Accuracy-first: for low-confidence detail/mismatch routing, ask once before execution.
        if (
            (intent.intent_confidence or 0.0) > 0
            and (intent.intent_confidence or 0.0) < 0.6
            and not intent.needs_clarification
            and intent.query_type in ("opportunity_detail", "mismatch_list")
        ):
            intent.needs_clarification = True
            intent.clarifying_question = (
                "为确保查询准确，请确认你想查看的是商机明细筛选结果，还是销售与AI差异清单？"
            )

        return intent
