"""Structured data retriever for Review Session Q&A.

Executes parameterized queries against CRM review tables and returns
structured context objects that can be serialized into LLM-readable text.
"""

import logging
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import Session, select, func

from app.models.crm_review import (
    CRMReviewKpiMetrics,
    CRMReviewOppBranchSnapshot,
    CRMReviewOppRiskProgress,
    CRMReviewRiskOpportunityRelation,
    CRMReviewSession,
)
from app.rag.chat.review.intent_router import ReviewIntent
from app.rag.chat.review.metric_catalog import (
    AMOUNT_METRIC_NAMES,
    METRIC_DISPLAY_NAMES,
)
from app.rag.chat.review.risk_type_helper import (
    ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT,
    load_risk_type_name_map,
    resolve_business_relation_type_names,
    split_business_vs_opportunity_risk_codes,
    validate_risk_type_codes,
)

logger = logging.getLogger(__name__)

AMOUNT_METRICS = AMOUNT_METRIC_NAMES

MISMATCH_QUERY_CONFIGS = {
    "stage": {
        "aliases": ("阶段", "stage", "项目阶段", "商机阶段"),
        "sales_field": "opportunity_stage",
        "ai_field": "ai_stage",
        "sales_label": "销售商机阶段",
        "ai_label": "AI商机阶段",
    },
    "forecast": {
        "aliases": ("预测", "判断", "commit", "forecast", "预测状态", "销售判断", "ai判断", "预测类型"),
        "sales_field": "forecast_type",
        "ai_field": "ai_commit",
        "sales_label": "销售预测状态",
        "ai_label": "AI预测状态",
    },
    "close_date": {
        "aliases": ("预计成交", "成交日期", "close date", "closing date"),
        "sales_field": "expected_closing_date",
        "ai_field": "ai_expected_closing_date",
        "sales_label": "销售预计成交日期",
        "ai_label": "AI预计成交日期",
    },
}


DETAIL_FIELD_ALIASES = {
    "owner_name": ("负责人", "销售", "owner", "对接人"),
    "opportunity_stage": ("阶段", "商机阶段", "stage"),
    "forecast_amount": ("签约金额", "金额", "amount", "amt"),
    "expected_closing_date": ("预计成交日期", "预计成交", "成交日期", "close date", "closing date"),
    "forecast_type": ("预测状态", "预测", "判断", "forecast", "commit"),
}


def _fmt_value(metric_name: str, value) -> str:
    if value is None:
        return "N/A"
    if metric_name == "pipeline_coverage":
        return f"{value:.2f}x"
    if metric_name == "opp_count":
        v = int(value) if isinstance(value, float) and value == int(value) else value
        return str(v)
    if metric_name in AMOUNT_METRICS and isinstance(value, (int, float)):
        if abs(value) >= 10000:
            return f"{value / 10000:.2f}万"
        return f"{value:,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _fmt_rate(rate) -> str:
    if rate is None:
        return ""
    pct = rate * 100 if abs(rate) < 10 else rate
    return f"{pct:+.1f}%"


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean_slot_value(v: str) -> str:
    """Remove trailing natural-language suffixes from extracted slot values."""
    value = (v or "").strip()
    if not value:
        return value
    value = re.sub(
        r"(?:的)?(?:商机|机会)(?:明细|列表|清单)?$|(?:明细|列表|清单)$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def _is_self_reference(v: str) -> bool:
    return (v or "").strip().lower() in ("我", "我自己", "本人", "自己", "me", "myself")


def _infer_opportunity_name_keyword(question: str) -> Optional[str]:
    """Heuristic extraction when the LLM did not set opportunity_name_keyword."""
    if not question:
        return None
    q = question.strip()
    patterns = [
        r"([\u4e00-\u9fff\w·（）()\-]{2,40}?)\s*的\s*(?:负责人|销售|对接人|owner)",
        r"(?:关于|针对)\s*([\u4e00-\u9fff\w·（）()\-]{2,40}?)(?:的)?(?:商机|机会)",
        r"([\u4e00-\u9fff\w·（）()\-]{2,40}?)\s*商机",
    ]
    for p in patterns:
        m = re.search(p, q, re.IGNORECASE)
        if m:
            hint = m.group(1).strip()
            if len(hint) >= 2:
                return hint
    return None


def _detect_mismatch_query_type(question: str) -> Optional[str]:
    """Detect mismatch list query type (stage / forecast / close_date)."""
    if not question:
        return None
    q = question.lower()
    has_sales = ("销售" in q) or ("业务" in q)
    has_ai = ("ai" in q) or ("智能" in q) or ("算法" in q)
    has_diff = any(k in q for k in ("不同", "不一致", "差异", "不一样", "冲突"))
    has_list = any(k in q for k in ("哪些", "列表", "清单", "列出"))
    has_opportunity = any(k in q for k in ("商机", "项目", "pipeline", "机会"))
    # "销售" may be omitted when users directly say "AI和商机阶段不一致".
    if not (has_ai and has_diff and (has_list or has_opportunity)):
        return None
    for diff_type, cfg in MISMATCH_QUERY_CONFIGS.items():
        if any(alias in q for alias in cfg["aliases"]):
            return diff_type
    return None


def _extract_detail_query_filters(question: str) -> Dict[str, Any]:
    """Extract simple field filters for typical opportunity detail queries."""
    if not question:
        return {}
    q = question.strip()
    ql = q.lower()
    has_opportunity_context = any(k in ql for k in ("商机", "项目", "pipeline", "机会"))
    if not has_opportunity_context:
        return {}

    filters: Dict[str, Any] = {}
    requested_fields: List[str] = []
    for field_name, aliases in DETAIL_FIELD_ALIASES.items():
        if any(alias in ql for alias in aliases):
            requested_fields.append(field_name)
    if requested_fields:
        filters["requested_fields"] = requested_fields

    # owner / stage / forecast: exact-ish token after "为/是/等于/叫做"
    token_extractors = {
        "owner_name": (
            r"(?:负责人|销售|owner|对接人)\s*(?:是|为|=|叫做)?\s*([^\s，。,；;：:]{2,40})",
            r"([^\s，。,；;：:]{2,40})\s*(?:负责|owner)",
        ),
        "opportunity_stage": (
            r"(?:阶段|商机阶段|stage)\s*(?:是|为|=)?\s*([^\s，。,；;：:]{1,30})",
        ),
        "forecast_type": (
            r"(?:预测状态|预测|判断|forecast|commit)\s*(?:是|为|=)?\s*([^\s，。,；;：:]{1,30})",
        ),
    }
    for field_name, patterns in token_extractors.items():
        for p in patterns:
            m = re.search(p, q, re.IGNORECASE)
            if m and m.group(1):
                cleaned = _clean_slot_value(m.group(1))
                if cleaned:
                    filters[field_name] = cleaned
                break

    # Explicit multi-value extraction (accuracy-first: only parse when "或/、/和/," appears).
    def _split_explicit_multi_values(text: str) -> List[str]:
        parts = re.split(r"[、,，/]|和|或", text)
        values = [_clean_slot_value(p) for p in parts if p and _clean_slot_value(p)]
        if len(values) <= 1:
            return []
        # Avoid over-segmentation when sentence particles are included.
        return [v for v in values if 1 <= len(v) <= 30]

    m_owner_multi = re.search(
        r"(?:负责人|销售|owner|对接人)\s*(?:是|为|=)?\s*([^\n。；;，,]{2,80})",
        q,
        re.IGNORECASE,
    )
    if m_owner_multi and m_owner_multi.group(1):
        owner_values = _split_explicit_multi_values(m_owner_multi.group(1))
        if owner_values:
            filters["owner_names"] = owner_values

    m_stage_multi = re.search(
        r"(?:阶段|商机阶段|stage)\s*(?:是|为|=)?\s*([^\n。；;，,]{1,80})",
        q,
        re.IGNORECASE,
    )
    if m_stage_multi and m_stage_multi.group(1):
        stage_values = _split_explicit_multi_values(m_stage_multi.group(1))
        if stage_values:
            filters["opportunity_stages"] = stage_values

    m_forecast_multi = re.search(
        r"(?:预测状态|预测|判断|forecast|commit)\s*(?:是|为|=)?\s*([^\n。；;，,]{1,80})",
        q,
        re.IGNORECASE,
    )
    if m_forecast_multi and m_forecast_multi.group(1):
        forecast_values = _split_explicit_multi_values(m_forecast_multi.group(1))
        if forecast_values:
            filters["forecast_types"] = forecast_values

    # expected close date: keep a lightweight string filter
    m_date = re.search(
        r"(?:预计成交日期|预计成交|成交日期|close date|closing date)\s*(?:是|为|在|=)?\s*([^\s，。,；;：:]{1,30})",
        q,
        re.IGNORECASE,
    )
    if m_date and m_date.group(1):
        filters["expected_closing_date"] = m_date.group(1).strip()

    # amount comparators
    m_amount = re.search(
        r"(?:签约金额|金额|amount|amt)\s*(大于等于|>=|不低于|不少于|至少|大于|>|小于等于|<=|不高于|至多|小于|<|等于|=)?\s*([0-9]+(?:\.[0-9]+)?)\s*(万|w|k|千)?",
        q,
        re.IGNORECASE,
    )
    if m_amount:
        raw_op = (m_amount.group(1) or "等于").strip()
        raw_value = float(m_amount.group(2))
        unit = (m_amount.group(3) or "").lower()
        if unit in ("万", "w"):
            raw_value *= 10000
        elif unit in ("k", "千"):
            raw_value *= 1000
        op_map = {
            "大于等于": "ge",
            ">=": "ge",
            "不低于": "ge",
            "不少于": "ge",
            "至少": "ge",
            "大于": "gt",
            ">": "gt",
            "小于等于": "le",
            "<=": "le",
            "不高于": "le",
            "至多": "le",
            "小于": "lt",
            "<": "lt",
            "等于": "eq",
            "=": "eq",
        }
        filters["forecast_amount_op"] = op_map.get(raw_op, "eq")
        filters["forecast_amount_value"] = raw_value

    # Explicit amount range: "金额在 10 到 20 万之间"
    m_amount_range = re.search(
        r"(?:签约金额|金额|amount|amt)[^\d]{0,6}([0-9]+(?:\.[0-9]+)?)\s*(万|w|k|千)?\s*(?:到|至|-|~)\s*([0-9]+(?:\.[0-9]+)?)\s*(万|w|k|千)?",
        q,
        re.IGNORECASE,
    )
    if m_amount_range:
        low = float(m_amount_range.group(1))
        low_unit = (m_amount_range.group(2) or "").lower()
        high = float(m_amount_range.group(3))
        high_unit = (m_amount_range.group(4) or "").lower()
        # If only one bound provides unit (e.g. "10到20万"), inherit it to the other bound.
        if not low_unit and high_unit:
            low_unit = high_unit
        if not high_unit and low_unit:
            high_unit = low_unit
        if low_unit in ("万", "w"):
            low *= 10000
        elif low_unit in ("k", "千"):
            low *= 1000
        if high_unit in ("万", "w"):
            high *= 10000
        elif high_unit in ("k", "千"):
            high *= 1000
        if low > high:
            low, high = high, low
        filters["forecast_amount_min"] = low
        filters["forecast_amount_max"] = high
        # Explicit range is more reliable than single comparator parse.
        filters.pop("forecast_amount_op", None)
        filters.pop("forecast_amount_value", None)

    return filters


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


class ReviewDataContext(BaseModel):
    """Container for structured retrieval results."""

    kpi_metrics: List[Dict[str, Any]] = Field(default_factory=list)
    snapshot_aggregations: List[Dict[str, Any]] = Field(default_factory=list)
    opportunity_snapshot_rows: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Per-opportunity branch snapshot rows (owner, forecast, stage)",
    )
    risks: List[Dict[str, Any]] = Field(default_factory=list)
    progresses: List[Dict[str, Any]] = Field(default_factory=list)
    comparison_data: Optional[Dict[str, Any]] = None
    query_note: Optional[str] = None

    def is_empty(self) -> bool:
        return (
            not self.kpi_metrics
            and not self.snapshot_aggregations
            and not self.opportunity_snapshot_rows
            and not self.risks
            and not self.progresses
            and not self.query_note
        )

    def to_context_text(self) -> str:
        """Serialize to LLM-readable structured text."""
        parts: List[str] = []
        if self.query_note:
            parts.append(self.query_note)
            parts.append("")

        if self.kpi_metrics:
            parts.append("### KPI 指标")
            parts.append("")
            parts.append("| 范围 | 指标 (metric_name) | 本期值 | 上期值 | 变化量 | 变化率 |")
            parts.append("|------|-------------------|--------|--------|--------|--------|")
            for m in self.kpi_metrics:
                mname = m.get("metric_name", "")
                cn = METRIC_DISPLAY_NAMES.get(mname, mname)
                scope = m.get("scope_name") or m.get("scope_id") or m.get("scope_type", "")
                cur = _fmt_value(mname, m.get("metric_value"))
                prev = _fmt_value(mname, m.get("metric_value_prev")) if m.get("metric_value_prev") is not None else "-"
                delta = _fmt_value(mname, m.get("metric_delta")) if m.get("metric_delta") is not None else "-"
                rate = _fmt_rate(m.get("metric_rate")) if m.get("metric_rate") is not None else "-"
                parts.append(f"| {scope} | {cn} ({mname}) | {cur} | {prev} | {delta} | {rate} |")

        if self.opportunity_snapshot_rows:
            parts.append("\n### 商机快照明细（本周期分支快照）")
            for row in self.opportunity_snapshot_rows:
                parts.append(
                    f"- 商机名称: {row.get('opportunity_name') or row.get('opportunity_id', 'N/A')} "
                    f"(opportunity_id={row.get('opportunity_id', '')})"
                )
                parts.append(
                    f"  客户: {row.get('account_name') or 'N/A'} | "
                    f"负责人: {row.get('owner_name') or 'N/A'} (owner_id={row.get('owner_id', '')})"
                )
                parts.append(
                    f"  预测状态: {row.get('forecast_type') or ''} / "
                    f"AI预测状态: {row.get('ai_commit') or ''} / "
                    f"销售商机阶段: {row.get('opportunity_stage') or ''} / "
                    f"AI商机阶段: {row.get('ai_stage') or ''} / "
                    f"签约金额={row.get('forecast_amount', 'N/A')} | "
                    f"预计成交日期: {row.get('expected_closing_date') or ''} / "
                    f"AI预计成交日期: {row.get('ai_expected_closing_date') or ''}"
                )
                if row.get("mismatch_type"):
                    parts.append(
                        f"  差异项: {row.get('sales_label')}={row.get('sales_value') or ''} / "
                        f"{row.get('ai_label')}={row.get('ai_value') or ''}"
                    )
        elif self.snapshot_aggregations:
            parts.append("\n### Opportunity Snapshot Aggregations")
            for agg in self.snapshot_aggregations:
                parts.append(
                    f"- {agg.get('group_label', 'N/A')}: "
                    f"count={agg.get('count', 0)}, "
                    f"total_amount={agg.get('total_amount', 0)}"
                )

        if self.comparison_data:
            parts.append("\n### Period Comparison")
            for key, val in self.comparison_data.items():
                parts.append(f"- {key}: {val}")

        return "\n".join(parts) if parts else "(No structured data available)"

    def to_risk_context_text(self) -> str:
        """Serialize risk/progress signals to LLM-readable text."""
        parts: List[str] = []

        if self.risks:
            parts.append("### Risks")
            for r in self.risks:
                severity = r.get("severity", "")
                sev_tag = f" [{severity}]" if severity else ""
                parts.append(
                    f"- {r.get('type_name', r.get('type_code', ''))}{sev_tag}: "
                    f"{r.get('summary', r.get('detail_description', 'N/A'))}"
                )
                if r.get("gap_description"):
                    parts.append(f"  Gap: {r['gap_description']}")
                if r.get("financial_impact") is not None:
                    parts.append(f"  Financial impact: {r['financial_impact']}")
                if r.get("solution"):
                    parts.append(f"  Suggested action: {r['solution']}")
                if r.get("detail_description") and r.get("detail_description") != r.get("summary"):
                    parts.append(f"  Detail: {r['detail_description']}")

        if self.progresses:
            parts.append("\n### Progress Signals")
            for p in self.progresses:
                parts.append(
                    f"- {p.get('type_name', p.get('type_code', ''))}: "
                    f"{p.get('summary', p.get('detail_description', 'N/A'))}"
                )
                if p.get("solution"):
                    parts.append(f"  Next step: {p['solution']}")

        return "\n".join(parts) if parts else "(No risk/progress signals)"


class ReviewDataRetriever:
    """Retrieves structured data from CRM review tables based on intent parameters."""

    def retrieve(
        self,
        db_session: Session,
        review_session: CRMReviewSession,
        intent: ReviewIntent,
        user_question: Optional[str] = None,
        current_owner_id: Optional[str] = None,
        current_owner_name: Optional[str] = None,
    ) -> ReviewDataContext:
        ctx = ReviewDataContext()

        session_id = review_session.unique_id
        snapshot_period = review_session.period
        question = user_question or ""
        plan = intent.query_plan or {}
        template_id = plan.get("template_id") if isinstance(plan, dict) else None
        query_type = plan.get("route") or intent.query_type
        mismatch_type = (
            plan.get("mismatch_type")
            or intent.mismatch_type
            or _detect_mismatch_query_type(question)
        )
        detail_filters = (
            (plan.get("detail_filters") if isinstance(plan.get("detail_filters"), dict) else None)
            or (intent.detail_filters if isinstance(intent.detail_filters, dict) else None)
            or _extract_detail_query_filters(question)
        )
        if query_type is None:
            if mismatch_type:
                query_type = "mismatch_list"
            elif detail_filters:
                query_type = "opportunity_detail"
            else:
                query_type = "kpi_aggregation"

        if query_type == "mismatch_list" and mismatch_type:
            cfg = MISMATCH_QUERY_CONFIGS[mismatch_type]
            ctx.opportunity_snapshot_rows = self._query_field_mismatch_opportunities(
                db_session=db_session,
                snapshot_period=snapshot_period,
                department_id=review_session.department_id,
                sales_field=cfg["sales_field"],
                ai_field=cfg["ai_field"],
                sales_label=cfg["sales_label"],
                ai_label=cfg["ai_label"],
                mismatch_type=mismatch_type,
                limit=200,
            )
            # For mismatch list queries, avoid falling back to KPI/aggregation views,
            # which can mislead the model into answering a different question.
            if ctx.opportunity_snapshot_rows:
                ctx.query_note = (
                    f"### 差异查询结果\n"
                    f"- 已按“{cfg['sales_label']} vs {cfg['ai_label']}”检索，"
                    f"共找到 {len(ctx.opportunity_snapshot_rows)} 个不一致商机。"
                )
            else:
                ctx.query_note = (
                    f"### 差异查询结果\n"
                    f"- 已按“{cfg['sales_label']} vs {cfg['ai_label']}”检索当前周期商机，"
                    f"未发现不一致记录。"
                )
        elif query_type == "opportunity_detail" and detail_filters:
            ctx.opportunity_snapshot_rows = self._query_typical_opportunity_details(
                db_session=db_session,
                snapshot_period=snapshot_period,
                department_id=review_session.department_id,
                detail_filters=detail_filters,
                current_owner_id=current_owner_id,
                current_owner_name=current_owner_name,
                limit=200,
            )
            if ctx.opportunity_snapshot_rows:
                ctx.query_note = (
                    "### 商机明细查询结果\n"
                    f"- 已按典型字段条件检索，共返回 {len(ctx.opportunity_snapshot_rows)} 条商机明细。"
                )
            else:
                ctx.query_note = (
                    "### 商机明细查询结果\n"
                    "- 已按典型字段条件检索当前周期商机，未匹配到结果。"
                )
        elif query_type == "risk_progress":
            # Risk/progress focused query: skip KPI/detail retrieval.
            risk_type_codes = plan.get("risk_type_codes") if isinstance(plan, dict) else None
            if not isinstance(risk_type_codes, list):
                risk_type_codes = None
            if template_id == "target_action_to_hit_goal":
                risk_type_codes = self._resolve_target_action_risk_type_codes(
                    db_session=db_session,
                    session_id=session_id,
                    department_id=review_session.department_id,
                )
            risk_universe_map = load_risk_type_name_map(db_session, None)
            if risk_type_codes:
                risk_type_codes, _ = validate_risk_type_codes(risk_type_codes, risk_universe_map)
            if not risk_type_codes and template_id not in (
                "target_action_to_hit_goal",
                "focus_risky_opportunities",
            ):
                risk_type_codes = list(ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT)
            if template_id == "target_action_to_hit_goal" and not risk_type_codes:
                ctx.risks = []
                ctx.progresses = []
                ctx.query_note = (
                    "### 达成目标建议\n"
                    "- 当前结论：commit 与 gap 基本持平，暂未触发重点达成风险。\n"
                    "- 建议动作：继续跟进在途商机，并持续关注经营洞察卡片中的新增风险。"
                )
                return ctx
            if template_id == "focus_risky_opportunities":
                risks_and_progress = self._query_risk_progress(
                    db_session,
                    session_id,
                    snapshot_period,
                    intent,
                    risk_type_codes=None,
                )
                ctx.risks = [r for r in risks_and_progress if r.get("record_type") == "RISK"]
                ctx.progresses = []
                opp_names: List[str] = []
                for r in ctx.risks:
                    name = (r.get("opportunity_name") or "").strip()
                    if name and name not in opp_names:
                        opp_names.append(name)
                opp_count = len(opp_names) if opp_names else len(ctx.risks)
                opp_list_text = "、".join(opp_names) if opp_names else "（未解析到商机名称）"
                if ctx.risks:
                    ctx.query_note = (
                        "### 重点关注商机\n"
                        f"- 当前结论：共识别到 {opp_count} 个需重点关注的风险商机。\n"
                        f"- 涉及商机：{opp_list_text}。\n"
                        "- 查看路径：点击商机评估Agent，筛选有风险的商机，点击商机详情。"
                    )
                else:
                    ctx.query_note = (
                        "### 重点关注商机\n"
                        "- 当前结论：暂未识别到风险商机。\n"
                        "- 建议动作：点击商机评估Agent，筛选有风险的商机，持续关注新增风险后再查看商机详情。"
                    )
                return ctx
            risk_name_map = load_risk_type_name_map(db_session, risk_type_codes)
            business_codes, opportunity_codes = split_business_vs_opportunity_risk_codes(risk_type_codes)
            ctx.progresses = []
            card_hint = "风险卡片"
            if business_codes and not opportunity_codes:
                relation_type_names = resolve_business_relation_type_names(business_codes)
                ctx.risks = self._query_risk_opportunity_relations(
                    db_session=db_session,
                    session_id=session_id,
                    snapshot_period=snapshot_period,
                    relation_type_names=relation_type_names,
                )
                card_hint = "达成风险卡片" if "业绩达成风险" in relation_type_names else "经营洞察卡片"
            elif opportunity_codes and not business_codes:
                risks_and_progress = self._query_risk_progress(
                    db_session, session_id, snapshot_period, intent, risk_type_codes=opportunity_codes
                )
                ctx.risks = [
                    r for r in risks_and_progress if r.get("record_type") in ("RISK", "OPP_SUMMARY")
                ]
                card_hint = "商机风险卡片"
            else:
                # Mixed selection: merge both chains and keep generic card hint.
                relation_type_names = resolve_business_relation_type_names(business_codes)
                relation_risks = self._query_risk_opportunity_relations(
                    db_session=db_session,
                    session_id=session_id,
                    snapshot_period=snapshot_period,
                    relation_type_names=relation_type_names,
                ) if business_codes else []
                opp_risks = self._query_risk_progress(
                    db_session, session_id, snapshot_period, intent, risk_type_codes=opportunity_codes
                ) if opportunity_codes else []
                opp_risks = [r for r in opp_risks if r.get("record_type") in ("RISK", "OPP_SUMMARY")]
                ctx.risks = relation_risks + opp_risks
                card_hint = "风险卡片"

            opp_names: List[str] = []
            for r in ctx.risks:
                name = (r.get("opportunity_name") or "").strip()
                if name and name not in opp_names:
                    opp_names.append(name)
            if ctx.risks:
                selected_risk_names = [
                    risk_name_map.get(code, code)
                    for code in (risk_type_codes or [])
                ]
                selected_risk_text = (
                    "、".join(selected_risk_names) if selected_risk_names else "达成相关风险"
                )
                opp_list_text = "、".join(opp_names) if opp_names else "（未解析到商机名称）"
                opp_count = len(opp_names) if opp_names else len(ctx.risks)
                if template_id == "achievement_risk_overview":
                    path_text = f"- 查看路径：进入经营洞察卡片，点击{card_hint}，再查看对应商机列表。"
                elif template_id == "target_action_to_hit_goal":
                    path_text = (
                        f"- 查看路径：进入经营洞察卡片，点击{card_hint}中的链接，"
                        "跳转到商机列表页查看明细。"
                    )
                else:
                    path_text = f"- 查看路径：进入经营洞察卡片，点击{card_hint}查看商机与风险详情。"
                ctx.query_note = (
                    "### 达成风险查询结果\n"
                    f"- 当前结论：共识别到 {opp_count} 个达成风险商机。\n"
                    f"- 风险与对象：风险类型为{selected_risk_text}，涉及商机包括 {opp_list_text}。\n"
                    f"{path_text}"
                )
            else:
                ctx.query_note = (
                    "### 达成风险查询结果\n"
                    "- 当前结论：暂未识别到达成风险商机。\n"
                    f"- 建议动作：进入经营洞察卡片，点击{card_hint}持续关注新增风险。"
                )
            return ctx
        else:
            if template_id == "owner_gap_ranking":
                ctx.kpi_metrics = self._query_owner_gap_ranking(
                    db_session=db_session,
                    session_id=session_id,
                )
                if ctx.kpi_metrics:
                    top_seller = (
                        ctx.kpi_metrics[0].get("scope_name")
                        or ctx.kpi_metrics[0].get("scope_id")
                        or "未知销售"
                    )
                    top_gap = _safe_float(ctx.kpi_metrics[0].get("metric_value")) or 0.0
                    other_sellers = [
                        m.get("scope_name") or m.get("scope_id") or "未知销售"
                        for m in ctx.kpi_metrics[1:4]
                    ]
                    other_hint = (
                        f"- 可进一步对比的销售：{'、'.join(other_sellers)}。"
                        if other_sellers
                        else "- 当前仅有 1 位销售数据，暂时没有可对比对象。"
                    )
                    ctx.query_note = (
                        "### 销售达成差额排名\n"
                        f"- 当前共统计 {len(ctx.kpi_metrics)} 位销售，已按达成差额从高到低排序。\n"
                        f"- 目前差额最大的是 {top_seller}，差额约 {top_gap:,.0f}。\n"
                        f"{other_hint}\n"
                        "- 查看路径：点击商机评估Agent，选择“人员分组”，查看每个销售下的具体商机列表。"
                    )
                else:
                    ctx.query_note = (
                        "### 销售达成差额排名\n"
                        "- 当前未查询到销售维度的达成差额数据。"
                    )
            else:
                ctx.kpi_metrics = self._query_kpi_metrics(
                    db_session, session_id, intent
                )
            ctx.opportunity_snapshot_rows = self._collect_opportunity_snapshot_rows(
                db_session,
                review_session,
                intent,
                user_question=question,
            )

        if query_type == "kpi_aggregation":
            ctx.snapshot_aggregations = self._query_snapshot_aggregations(
                db_session,
                snapshot_period,
                intent,
                skip_opportunity_detail=bool(ctx.opportunity_snapshot_rows),
            )

        risks_and_progress = self._query_risk_progress(
            db_session, session_id, snapshot_period, intent, risk_type_codes=None
        )
        ctx.risks = [
            r for r in risks_and_progress if r.get("record_type") in ("RISK", "OPP_SUMMARY")
        ]
        ctx.progresses = [
            r for r in risks_and_progress if r.get("record_type") == "PROGRESS"
        ]

        if intent.time_comparison == "wow":
            ctx.comparison_data = self._build_wow_comparison(ctx.kpi_metrics)

        return ctx

    def _query_typical_opportunity_details(
        self,
        db_session: Session,
        snapshot_period: str,
        department_id: Optional[str],
        detail_filters: Dict[str, Any],
        current_owner_id: Optional[str] = None,
        current_owner_name: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        S = CRMReviewOppBranchSnapshot
        stmt = select(S).where(S.snapshot_period == snapshot_period)
        if department_id:
            stmt = stmt.where(S.owner_department_id == department_id)

        owner_name = (detail_filters.get("owner_name") or "").strip()
        if owner_name:
            if _is_self_reference(owner_name):
                if current_owner_id:
                    stmt = stmt.where(S.owner_id == current_owner_id)
                elif current_owner_name:
                    stmt = stmt.where(S.owner_name.like(f"%{_escape_like(current_owner_name)}%"))
            else:
                stmt = stmt.where(S.owner_name.like(f"%{_escape_like(owner_name)}%"))
        owner_names = detail_filters.get("owner_names") or []
        if owner_names:
            owner_conditions = [
                (
                    (S.owner_id == current_owner_id)
                    if (_is_self_reference(str(name).strip()) and current_owner_id)
                    else (
                        S.owner_name.like(f"%{_escape_like(current_owner_name)}%")
                        if (_is_self_reference(str(name).strip()) and current_owner_name)
                        else S.owner_name.like(f"%{_escape_like(str(name).strip())}%")
                    )
                )
                for name in owner_names
                if str(name).strip()
            ]
            if owner_conditions:
                stmt = stmt.where(or_(*owner_conditions))

        stage = (detail_filters.get("opportunity_stage") or "").strip()
        if stage:
            stmt = stmt.where(S.opportunity_stage.like(f"%{_escape_like(stage)}%"))
        stage_values = detail_filters.get("opportunity_stages") or []
        if stage_values:
            stage_conditions = [
                S.opportunity_stage.like(f"%{_escape_like(str(v).strip())}%")
                for v in stage_values
                if str(v).strip()
            ]
            if stage_conditions:
                stmt = stmt.where(or_(*stage_conditions))

        forecast_type = (detail_filters.get("forecast_type") or "").strip()
        if forecast_type:
            stmt = stmt.where(S.forecast_type.like(f"%{_escape_like(forecast_type)}%"))
        forecast_values = detail_filters.get("forecast_types") or []
        if forecast_values:
            forecast_conditions = [
                S.forecast_type.like(f"%{_escape_like(str(v).strip())}%")
                for v in forecast_values
                if str(v).strip()
            ]
            if forecast_conditions:
                stmt = stmt.where(or_(*forecast_conditions))

        expected_closing_date = (detail_filters.get("expected_closing_date") or "").strip()
        if expected_closing_date:
            stmt = stmt.where(
                S.expected_closing_date.like(f"%{_escape_like(expected_closing_date)}%")
            )

        amount_value = detail_filters.get("forecast_amount_value")
        amount_op = detail_filters.get("forecast_amount_op")
        amount_min = detail_filters.get("forecast_amount_min")
        amount_max = detail_filters.get("forecast_amount_max")
        if amount_value is not None:
            if amount_op == "ge":
                stmt = stmt.where(S.forecast_amount >= amount_value)
            elif amount_op == "gt":
                stmt = stmt.where(S.forecast_amount > amount_value)
            elif amount_op == "le":
                stmt = stmt.where(S.forecast_amount <= amount_value)
            elif amount_op == "lt":
                stmt = stmt.where(S.forecast_amount < amount_value)
            else:
                stmt = stmt.where(S.forecast_amount == amount_value)
        if amount_min is not None and amount_max is not None:
            stmt = stmt.where(S.forecast_amount >= amount_min, S.forecast_amount <= amount_max)

        stmt = stmt.order_by(S.forecast_amount.desc(), S.opportunity_name).limit(limit)
        rows = list(db_session.exec(stmt).all())
        if not rows and department_id:
            # fallback to full scope to reduce false negatives from dept slicing
            stmt2 = select(S).where(S.snapshot_period == snapshot_period)
            if owner_name:
                if _is_self_reference(owner_name):
                    if current_owner_id:
                        stmt2 = stmt2.where(S.owner_id == current_owner_id)
                    elif current_owner_name:
                        stmt2 = stmt2.where(S.owner_name.like(f"%{_escape_like(current_owner_name)}%"))
                else:
                    stmt2 = stmt2.where(S.owner_name.like(f"%{_escape_like(owner_name)}%"))
            if owner_names:
                owner_conditions = [
                    (
                        (S.owner_id == current_owner_id)
                        if (_is_self_reference(str(name).strip()) and current_owner_id)
                        else (
                            S.owner_name.like(f"%{_escape_like(current_owner_name)}%")
                            if (_is_self_reference(str(name).strip()) and current_owner_name)
                            else S.owner_name.like(f"%{_escape_like(str(name).strip())}%")
                        )
                    )
                    for name in owner_names
                    if str(name).strip()
                ]
                if owner_conditions:
                    stmt2 = stmt2.where(or_(*owner_conditions))
            if stage:
                stmt2 = stmt2.where(S.opportunity_stage.like(f"%{_escape_like(stage)}%"))
            if stage_values:
                stage_conditions = [
                    S.opportunity_stage.like(f"%{_escape_like(str(v).strip())}%")
                    for v in stage_values
                    if str(v).strip()
                ]
                if stage_conditions:
                    stmt2 = stmt2.where(or_(*stage_conditions))
            if forecast_type:
                stmt2 = stmt2.where(
                    S.forecast_type.like(f"%{_escape_like(forecast_type)}%")
                )
            if forecast_values:
                forecast_conditions = [
                    S.forecast_type.like(f"%{_escape_like(str(v).strip())}%")
                    for v in forecast_values
                    if str(v).strip()
                ]
                if forecast_conditions:
                    stmt2 = stmt2.where(or_(*forecast_conditions))
            if expected_closing_date:
                stmt2 = stmt2.where(
                    S.expected_closing_date.like(
                        f"%{_escape_like(expected_closing_date)}%"
                    )
                )
            if amount_value is not None:
                if amount_op == "ge":
                    stmt2 = stmt2.where(S.forecast_amount >= amount_value)
                elif amount_op == "gt":
                    stmt2 = stmt2.where(S.forecast_amount > amount_value)
                elif amount_op == "le":
                    stmt2 = stmt2.where(S.forecast_amount <= amount_value)
                elif amount_op == "lt":
                    stmt2 = stmt2.where(S.forecast_amount < amount_value)
                else:
                    stmt2 = stmt2.where(S.forecast_amount == amount_value)
            if amount_min is not None and amount_max is not None:
                stmt2 = stmt2.where(S.forecast_amount >= amount_min, S.forecast_amount <= amount_max)
            stmt2 = stmt2.order_by(S.forecast_amount.desc(), S.opportunity_name).limit(limit)
            rows = list(db_session.exec(stmt2).all())
        return [self._snapshot_row_to_detail(r) for r in rows]

    def _query_kpi_metrics(
        self,
        db_session: Session,
        session_id: str,
        intent: ReviewIntent,
    ) -> List[Dict[str, Any]]:
        stmt = select(CRMReviewKpiMetrics).where(
            CRMReviewKpiMetrics.session_id == session_id
        )

        if intent.scope_type:
            stmt = stmt.where(CRMReviewKpiMetrics.scope_type == intent.scope_type)
        if intent.scope_id:
            stmt = stmt.where(CRMReviewKpiMetrics.scope_id == intent.scope_id)
        if intent.metric_names:
            stmt = stmt.where(CRMReviewKpiMetrics.metric_name.in_(intent.metric_names))

        stmt = stmt.order_by(
            CRMReviewKpiMetrics.scope_type,
            CRMReviewKpiMetrics.metric_category,
            CRMReviewKpiMetrics.metric_name,
        )

        rows = db_session.exec(stmt).all()
        return [
            {
                "scope_type": r.scope_type,
                "scope_id": r.scope_id,
                "scope_name": r.scope_name,
                "metric_category": r.metric_category,
                "metric_name": r.metric_name,
                "metric_value": _safe_float(r.metric_value),
                "metric_value_prev": _safe_float(r.metric_value_prev),
                "metric_delta": _safe_float(r.metric_delta),
                "metric_rate": _safe_float(r.metric_rate),
                "metric_unit": r.metric_unit,
                "metric_content": r.metric_content,
                "calc_phase": r.calc_phase,
            }
            for r in rows
        ]

    def _query_owner_gap_ranking(
        self,
        db_session: Session,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        K = CRMReviewKpiMetrics
        stmt = select(K).where(
            K.session_id == session_id,
            K.scope_type == "owner",
            K.metric_name == "gap",
        )
        rows = list(db_session.exec(stmt).all())
        rows = [r for r in rows if r.metric_value is not None]
        rows.sort(key=lambda r: float(r.metric_value), reverse=True)
        return [
            {
                "scope_type": r.scope_type,
                "scope_id": r.scope_id,
                "scope_name": r.scope_name,
                "metric_category": r.metric_category,
                "metric_name": r.metric_name,
                "metric_value": _safe_float(r.metric_value),
                "metric_value_prev": _safe_float(r.metric_value_prev),
                "metric_delta": _safe_float(r.metric_delta),
                "metric_rate": _safe_float(r.metric_rate),
                "metric_unit": r.metric_unit,
                "metric_content": r.metric_content,
                "calc_phase": r.calc_phase,
            }
            for r in rows
        ]

    def _resolve_target_action_risk_type_codes(
        self,
        db_session: Session,
        session_id: str,
        department_id: Optional[str],
    ) -> List[str]:
        """Resolve risk type by comparing commit_sales vs gap for current session."""
        K = CRMReviewKpiMetrics
        stmt = select(K).where(
            K.session_id == session_id,
            K.metric_name.in_(["gap", "commit_sales"]),
        )
        rows = list(db_session.exec(stmt).all())
        if not rows:
            return []

        def _sum_metric(metric: str, scope_type: Optional[str], scope_id: Optional[str] = None) -> Optional[float]:
            values = []
            for r in rows:
                if r.metric_name != metric:
                    continue
                if scope_type is not None and r.scope_type != scope_type:
                    continue
                if scope_id is not None and r.scope_id != scope_id:
                    continue
                if r.metric_value is not None:
                    values.append(float(r.metric_value))
            if not values:
                return None
            return float(sum(values))

        gap = _sum_metric("gap", "department", department_id) if department_id else None
        commit = _sum_metric("commit_sales", "department", department_id) if department_id else None
        if gap is None or commit is None:
            gap = _sum_metric("gap", "company")
            commit = _sum_metric("commit_sales", "company")
        if gap is None or commit is None:
            gap = _sum_metric("gap", None)
            commit = _sum_metric("commit_sales", None)
        if gap is None or commit is None:
            return []
        if commit >= gap:
            return ["ACHIEVEMENT_GAP_COMMIT_HIGH_RISK"]
        if commit < gap:
            return ["ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT"]
        return []

    def _query_risk_opportunity_relations(
        self,
        db_session: Session,
        session_id: str,
        snapshot_period: str,
        relation_type_names: List[str],
    ) -> List[Dict[str, Any]]:
        RR = CRMReviewRiskOpportunityRelation
        stmt = select(RR).where(
            RR.session_id == session_id,
            RR.snapshot_period == snapshot_period,
        )
        if relation_type_names:
            stmt = stmt.where(RR.type_name.in_(relation_type_names))
        rows = list(db_session.exec(stmt).all())
        opportunity_ids = [r.opportunity_id for r in rows if getattr(r, "opportunity_id", None)]
        opp_name_map: Dict[str, str] = {}
        if opportunity_ids:
            S = CRMReviewOppBranchSnapshot
            opp_rows = db_session.exec(
                select(S).where(
                    S.snapshot_period == snapshot_period,
                    S.opportunity_id.in_(opportunity_ids),
                )
            ).all()
            opp_name_map = {
                o.opportunity_id: (o.opportunity_name or "")
                for o in opp_rows
                if getattr(o, "opportunity_id", None)
            }
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "record_type": "RISK",
                    "type_name": r.type_name or "",
                    "opportunity_id": r.opportunity_id,
                    "opportunity_name": opp_name_map.get(r.opportunity_id or "", ""),
                    "owner_id": r.owner_id,
                    "scope_type": "department_or_company",
                }
            )
        return out

    def _snapshot_row_to_detail(self, row: CRMReviewOppBranchSnapshot) -> Dict[str, Any]:
        return {
            "opportunity_id": row.opportunity_id,
            "opportunity_name": row.opportunity_name,
            "account_name": row.account_name,
            "owner_name": row.owner_name,
            "owner_id": row.owner_id,
            "forecast_type": row.forecast_type,
            "ai_commit": row.ai_commit,
            "forecast_amount": _safe_float(row.forecast_amount),
            "opportunity_stage": row.opportunity_stage,
            "ai_stage": row.ai_stage,
            "expected_closing_date": row.expected_closing_date,
            "ai_expected_closing_date": row.ai_expected_closing_date,
        }

    def _query_field_mismatch_opportunities(
        self,
        db_session: Session,
        snapshot_period: str,
        department_id: Optional[str],
        sales_field: str,
        ai_field: str,
        sales_label: str,
        ai_label: str,
        mismatch_type: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        S = CRMReviewOppBranchSnapshot
        sales_col = getattr(S, sales_field)
        ai_col = getattr(S, ai_field)
        stmt = select(S).where(
            S.snapshot_period == snapshot_period,
            sales_col.is_not(None),
            ai_col.is_not(None),
            sales_col != ai_col,
        )
        if department_id:
            stmt = stmt.where(S.owner_department_id == department_id)
        stmt = stmt.order_by(S.opportunity_name).limit(limit)
        rows = list(db_session.exec(stmt).all())
        if not rows and department_id:
            # Fallback to full period if departmental scope has no mismatch rows.
            stmt2 = (
                select(S)
                .where(
                    S.snapshot_period == snapshot_period,
                    sales_col.is_not(None),
                    ai_col.is_not(None),
                    sales_col != ai_col,
                )
                .order_by(S.opportunity_name)
                .limit(limit)
            )
            rows = list(db_session.exec(stmt2).all())
        detailed_rows: List[Dict[str, Any]] = []
        for r in rows:
            row = self._snapshot_row_to_detail(r)
            row.update(
                {
                    "mismatch_type": mismatch_type,
                    "sales_label": sales_label,
                    "ai_label": ai_label,
                    "sales_value": row.get(sales_field),
                    "ai_value": row.get(ai_field),
                }
            )
            detailed_rows.append(row)
        return detailed_rows

    def _get_snapshot_by_opportunity_id(
        self,
        db_session: Session,
        snapshot_period: str,
        opportunity_id: str,
        department_id: Optional[str],
    ) -> Optional[CRMReviewOppBranchSnapshot]:
        S = CRMReviewOppBranchSnapshot
        stmt = select(S).where(
            S.snapshot_period == snapshot_period,
            S.opportunity_id == opportunity_id,
        )
        if department_id:
            stmt = stmt.where(S.owner_department_id == department_id)
        row = db_session.exec(stmt).first()
        if row:
            return row
        return db_session.exec(
            select(S).where(
                S.snapshot_period == snapshot_period,
                S.opportunity_id == opportunity_id,
            )
        ).first()

    def _search_snapshots_by_name_keyword(
        self,
        db_session: Session,
        snapshot_period: str,
        keyword: str,
        department_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        S = CRMReviewOppBranchSnapshot
        pattern = f"%{_escape_like(keyword)}%"
        stmt = (
            select(S)
            .where(S.snapshot_period == snapshot_period)
            .where(
                or_(
                    S.opportunity_name.like(pattern),
                    S.account_name.like(pattern),
                )
            )
        )
        if department_id:
            stmt = stmt.where(S.owner_department_id == department_id)
        stmt = stmt.limit(25)
        rows = list(db_session.exec(stmt).all())
        if not rows and department_id:
            stmt2 = (
                select(S)
                .where(S.snapshot_period == snapshot_period)
                .where(
                    or_(
                        S.opportunity_name.like(pattern),
                        S.account_name.like(pattern),
                    )
                )
                .limit(25)
            )
            rows = list(db_session.exec(stmt2).all())
        return [self._snapshot_row_to_detail(r) for r in rows]

    def _collect_opportunity_snapshot_rows(
        self,
        db_session: Session,
        review_session: CRMReviewSession,
        intent: ReviewIntent,
        user_question: str,
    ) -> List[Dict[str, Any]]:
        period = review_session.period
        dept_id = review_session.department_id

        if intent.opportunity_id:
            row = self._get_snapshot_by_opportunity_id(
                db_session, period, intent.opportunity_id, dept_id
            )
            if row:
                return [self._snapshot_row_to_detail(row)]
            return []

        kw = (intent.opportunity_name_keyword or "").strip()
        if not kw and user_question:
            kw = (_infer_opportunity_name_keyword(user_question) or "").strip()
        if not kw:
            return []

        return self._search_snapshots_by_name_keyword(
            db_session, period, kw, dept_id
        )

    def _query_snapshot_aggregations(
        self,
        db_session: Session,
        snapshot_period: str,
        intent: ReviewIntent,
        skip_opportunity_detail: bool = False,
    ) -> List[Dict[str, Any]]:
        S = CRMReviewOppBranchSnapshot

        group_col = S.forecast_type
        label_col = S.forecast_type

        if intent.scope_type == "owner" and intent.scope_id:
            stmt = (
                select(
                    group_col,
                    func.count().label("count"),
                    func.sum(S.forecast_amount).label("total_amount"),
                )
                .where(S.snapshot_period == snapshot_period)
                .where(S.owner_id == intent.scope_id)
                .group_by(group_col)
            )
        elif intent.opportunity_id and not skip_opportunity_detail:
            stmt = select(S).where(
                S.snapshot_period == snapshot_period,
                S.opportunity_id == intent.opportunity_id,
            )
            row = db_session.exec(stmt).first()
            if row:
                return [
                    {
                        "group_label": row.opportunity_name or row.opportunity_id,
                        "count": 1,
                        "total_amount": _safe_float(row.forecast_amount),
                        "forecast_type": row.forecast_type,
                        "stage": row.opportunity_stage,
                        "expected_closing_date": row.expected_closing_date,
                        "owner_name": row.owner_name,
                        "owner_id": row.owner_id,
                    }
                ]
            return []
        else:
            stmt = (
                select(
                    group_col,
                    func.count().label("count"),
                    func.sum(S.forecast_amount).label("total_amount"),
                )
                .where(S.snapshot_period == snapshot_period)
                .group_by(group_col)
            )

        rows = db_session.exec(stmt).all()
        return [
            {
                "group_label": r[0] or "(empty)",
                "count": r[1],
                "total_amount": _safe_float(r[2]),
            }
            for r in rows
        ]

    def _query_risk_progress(
        self,
        db_session: Session,
        session_id: str,
        snapshot_period: str,
        intent: ReviewIntent,
        risk_type_codes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        R = CRMReviewOppRiskProgress
        stmt = select(R).where(
            R.session_id == session_id,
            R.snapshot_period == snapshot_period,
        )

        if intent.scope_type:
            stmt = stmt.where(R.scope_type == intent.scope_type)
        if intent.scope_id:
            stmt = stmt.where(R.scope_id == intent.scope_id)
        if intent.opportunity_id:
            stmt = stmt.where(R.opportunity_id == intent.opportunity_id)
        if risk_type_codes:
            stmt = stmt.where(R.type_code.in_(risk_type_codes))

        stmt = stmt.order_by(R.record_type, R.type_code)

        rows = db_session.exec(stmt).all()
        opportunity_ids = [
            r.opportunity_id for r in rows
            if getattr(r, "opportunity_id", None)
        ]
        opp_name_map: Dict[str, str] = {}
        if opportunity_ids:
            S = CRMReviewOppBranchSnapshot
            opp_rows = db_session.exec(
                select(S).where(
                    S.snapshot_period == snapshot_period,
                    S.opportunity_id.in_(opportunity_ids),
                )
            ).all()
            opp_name_map = {
                o.opportunity_id: (o.opportunity_name or "")
                for o in opp_rows
                if getattr(o, "opportunity_id", None)
            }
        return [
            {
                "record_type": r.record_type,
                "type_code": r.type_code,
                "type_name": r.type_name,
                "category": r.category,
                "severity": r.severity,
                "source": r.source,
                "summary": r.summary,
                "gap_description": r.gap_description,
                "detail_description": r.detail_description,
                "solution": r.solution,
                "financial_impact": _safe_float(r.financial_impact),
                "previous_value": _safe_float(r.previous_value),
                "current_value": _safe_float(r.current_value),
                "rate_of_change": _safe_float(r.rate_of_change),
                "status": r.status,
                "scope_type": r.scope_type,
                "scope_id": r.scope_id,
                "opportunity_id": r.opportunity_id,
                "opportunity_name": opp_name_map.get(r.opportunity_id or "", ""),
                "owner_id": r.owner_id,
            }
            for r in rows
        ]

    @staticmethod
    def _build_wow_comparison(
        kpi_metrics: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        comparison: Dict[str, Any] = {}
        for m in kpi_metrics:
            if m.get("metric_delta") is not None:
                key = f"{m.get('scope_name', m.get('scope_type', ''))} / {m['metric_name']}"
                comparison[key] = {
                    "current": m["metric_value"],
                    "previous": m["metric_value_prev"],
                    "delta": m["metric_delta"],
                    "rate": m.get("metric_rate"),
                }
        return comparison if comparison else None
