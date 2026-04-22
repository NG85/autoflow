"""Structured data retriever for Review Session Q&A.

Executes parameterized queries against CRM review tables and returns
structured context objects that can be serialized into LLM-readable text.
"""

import logging
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

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
from app.rag.chat.review.query_plan_contract import (
    ALLOWED_QUERY_ROUTES,
    ALLOWED_SCOPE_TYPES,
    ALLOWED_TIME_MODES,
)
from app.rag.chat.review.risk_type_helper import (
    ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT,
    BUSINESS_RISK_TYPE_CODES,
    load_risk_code_meta,
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
    risk_category_breakdown: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Per category_group stats for opportunity-level RISK rows.",
    )
    comparison_data: Optional[Dict[str, Any]] = None
    query_note: Optional[str] = None

    def is_empty(self) -> bool:
        return (
            not self.kpi_metrics
            and not self.snapshot_aggregations
            and not self.opportunity_snapshot_rows
            and not self.risks
            and not self.progresses
            and not self.risk_category_breakdown
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
            parts.append("\n### 商机分组汇总")
            for agg in self.snapshot_aggregations:
                parts.append(
                    f"- {agg.get('group_label', 'N/A')}："
                    f"商机数={agg.get('count', 0)}，"
                    f"签约金额合计={agg.get('total_amount', 0)}"
                )

        if self.comparison_data:
            parts.append("\n### 周期对比")
            for key, val in self.comparison_data.items():
                parts.append(f"- {key}: {val}")

        if self.risk_category_breakdown:
            parts.append("\n### 商机风险分类统计")
            for block in self.risk_category_breakdown:
                cg = block.get("category_group", "")
                cnt = block.get("opportunity_count", 0)
                names = block.get("opportunity_names") or []
                shown = names[:25]
                suffix = f" 等共 {cnt} 个" if len(names) > len(shown) else ""
                parts.append(
                    f"- **{cg}**：{cnt} 个商机 — "
                    f"{('、'.join(shown) if shown else '（无商机名）')}{suffix}"
                )
                for t in block.get("by_risk_type") or []:
                    tc = t.get("type_code", "")
                    tname = t.get("name_zh", tc)
                    tcnt = t.get("opportunity_count", 0)
                    tnames = t.get("opportunity_names") or []
                    tshown = tnames[:15]
                    tsfx = f" 等共 {tcnt} 个" if len(tnames) > len(tshown) else ""
                    parts.append(
                        f"  - {tname}（{tc}）：{tcnt} 个商机 — "
                        f"{('、'.join(tshown) if tshown else '（无商机名）')}{tsfx}"
                    )

        return "\n".join(parts) if parts else "（暂时没有可展示的数据）"

    def to_risk_context_text(self) -> str:
        """Serialize risk/progress signals to LLM-readable text."""
        parts: List[str] = []

        if self.risks:
            parts.append("### 风险信号")
            for r in self.risks:
                severity = r.get("severity", "")
                sev_tag = f" [{severity}]" if severity else ""
                parts.append(
                    f"- {r.get('type_name', r.get('type_code', ''))}{sev_tag}: "
                    f"{r.get('summary', r.get('detail_description', 'N/A'))}"
                )
                if r.get("gap_description"):
                    parts.append(f"  差距说明：{r['gap_description']}")
                if r.get("financial_impact") is not None:
                    parts.append(f"  影响金额：{r['financial_impact']}")
                if r.get("solution"):
                    parts.append(f"  建议动作：{r['solution']}")
                if r.get("detail_description") and r.get("detail_description") != r.get("summary"):
                    parts.append(f"  详情：{r['detail_description']}")

        if self.progresses:
            parts.append("\n### 进展信号")
            for p in self.progresses:
                parts.append(
                    f"- {p.get('type_name', p.get('type_code', ''))}: "
                    f"{p.get('summary', p.get('detail_description', 'N/A'))}"
                )
                if p.get("solution"):
                    parts.append(f"  下一步建议：{p['solution']}")

        return "\n".join(parts) if parts else "（暂无风险/进展信号）"


class ReviewDataRetriever:
    """Retrieves structured data from CRM review tables based on intent parameters."""

    @staticmethod
    def _build_opportunity_list_preview(
        rows: List[Dict[str, Any]],
        title: str,
        max_items: int = 8,
    ) -> str:
        if not rows:
            return ""
        lines = [f"### {title}（Top {min(max_items, len(rows))}）"]
        for idx, row in enumerate(rows[:max_items], 1):
            opp_name = row.get("opportunity_name") or row.get("opportunity_id") or "未知商机"
            owner_name = row.get("owner_name") or "未知负责人"
            forecast_amount = row.get("forecast_amount")
            amount_text = (
                _fmt_value("commit_sales", forecast_amount)
                if forecast_amount is not None
                else "N/A"
            )
            mismatch_text = ""
            if row.get("mismatch_type"):
                mismatch_text = (
                    f"；差异={row.get('sales_label')}:{row.get('sales_value') or 'N/A'}"
                    f" vs {row.get('ai_label')}:{row.get('ai_value') or 'N/A'}"
                )
            lines.append(
                f"- {idx}. {opp_name}｜负责人:{owner_name}｜金额:{amount_text}{mismatch_text}"
            )
        if len(rows) > max_items:
            lines.append(f"- 其余 {len(rows) - max_items} 条请在明细页查看。")
        return "\n".join(lines)

    @staticmethod
    def _build_owner_gap_preview(
        rows: List[Dict[str, Any]],
        max_items: int = 8,
    ) -> str:
        if not rows:
            return ""
        lines = [f"### 销售差额清单（Top {min(max_items, len(rows))}）"]
        for idx, row in enumerate(rows[:max_items], 1):
            owner = row.get("scope_name") or row.get("scope_id") or "未知销售"
            gap_value = _safe_float(row.get("metric_value")) or 0.0
            lines.append(f"- {idx}. {owner}｜差额:{gap_value:,.0f}")
        if len(rows) > max_items:
            lines.append(f"- 其余 {len(rows) - max_items} 位销售请在人员分组查看。")
        return "\n".join(lines)

    @staticmethod
    def _normalize_execution_scope_and_time(
        intent: ReviewIntent,
        current_owner_id: Optional[str] = None,
    ) -> Tuple[str, str, str, Dict[str, Any]]:
        """Normalize query_plan scope/time for route execution."""
        plan = intent.query_plan if isinstance(intent.query_plan, dict) else {}
        plan_time_scope_obj = (
            plan.get("time_scope")
            if isinstance(plan.get("time_scope"), dict)
            else {}
        )
        time_mode = str(
            plan_time_scope_obj.get("mode")
            or intent.time_comparison
            or "current_only"
        ).strip().lower()
        if time_mode not in ALLOWED_TIME_MODES:
            time_mode = "current_only"
        plan_scope_obj = (
            plan.get("scope")
            if isinstance(plan.get("scope"), dict)
            else {}
        )
        effective_scope_type = str(
            plan_scope_obj.get("type")
            or intent.scope_type
            or ""
        ).strip().lower()
        if effective_scope_type not in ALLOWED_SCOPE_TYPES:
            effective_scope_type = ""
        effective_scope_id = str(
            plan_scope_obj.get("id")
            or intent.scope_id
            or ""
        ).strip()
        if effective_scope_type == "owner" and effective_scope_id == "__CURRENT_USER__":
            effective_scope_id = str(current_owner_id or "").strip()
        return time_mode, effective_scope_type, effective_scope_id, plan_scope_obj

    def retrieve(
        self,
        db_session: Session,
        review_session: CRMReviewSession,
        intent: ReviewIntent,
        user_question: Optional[str] = None,
        current_owner_id: Optional[str] = None,
        current_owner_name: Optional[str] = None,
        enforced_owner_id: Optional[str] = None,
    ) -> ReviewDataContext:
        ctx = ReviewDataContext()

        session_id = review_session.unique_id
        snapshot_period = review_session.period
        question = user_question or ""
        plan = intent.query_plan or {}
        template_id = plan.get("template_id") if isinstance(plan, dict) else None
        query_type = str(plan.get("route") or intent.query_type or "").strip().lower()
        if query_type not in ALLOWED_QUERY_ROUTES:
            query_type = None
        time_mode, effective_scope_type, effective_scope_id, plan_scope_obj = (
            self._normalize_execution_scope_and_time(
                intent=intent,
                current_owner_id=current_owner_id,
            )
        )
        if enforced_owner_id:
            enforced_owner_id = str(enforced_owner_id).strip()
            if enforced_owner_id:
                effective_scope_type = "owner"
                effective_scope_id = enforced_owner_id
                if not isinstance(plan_scope_obj, dict):
                    plan_scope_obj = {}
                plan_scope_obj["type"] = "owner"
                plan_scope_obj["id"] = enforced_owner_id
        if time_mode not in ("", "current_only"):
            ctx.query_note = (
                "### 时间范围说明\n"
                "- 当前 review 问答先只支持本期数据。\n"
                "- 请改用本期口径提问，我会基于当前会话给你结果。"
            )
            return ctx
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
        # Replay execution contract (M2):
        # - All routes should read scope from query_plan.scope first.
        # - The normalized execution intent below is the single source of truth
        #   passed into downstream query functions.
        # - Do not use raw `intent.scope_*` directly in new branches, otherwise
        #   replay/audit behavior may diverge across routes.
        intent_for_execution = intent.model_copy(deep=True)
        intent_for_execution.scope_type = effective_scope_type or None
        intent_for_execution.scope_id = effective_scope_id or None
        if query_type is None:
            if mismatch_type:
                query_type = "mismatch_list"
            elif detail_filters:
                query_type = "opportunity_detail"
            else:
                query_type = "kpi_aggregation"

        # Route-level rule:
        # use `intent_for_execution` (or effective_scope_*) for filters so
        # mismatch_list/opportunity_detail/risk_progress/kpi_aggregation remain
        # consistent under query_plan replay.
        if query_type == "mismatch_list" and mismatch_type:
            cfg = MISMATCH_QUERY_CONFIGS[mismatch_type]
            ctx.opportunity_snapshot_rows = self._query_field_mismatch_opportunities(
                db_session=db_session,
                snapshot_period=snapshot_period,
                department_id=review_session.department_id,
                owner_id=effective_scope_id if effective_scope_type == "owner" else None,
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
                list_preview = self._build_opportunity_list_preview(
                    ctx.opportunity_snapshot_rows,
                    title="差异商机清单",
                )
                if list_preview:
                    ctx.query_note = f"{ctx.query_note}\n\n{list_preview}"
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
                scope_owner_id=effective_scope_id if effective_scope_type == "owner" else None,
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
                list_preview = self._build_opportunity_list_preview(
                    ctx.opportunity_snapshot_rows,
                    title="商机明细清单",
                )
                if list_preview:
                    ctx.query_note = f"{ctx.query_note}\n\n{list_preview}"
            else:
                ctx.query_note = (
                    "### 商机明细查询结果\n"
                    "- 已按典型字段条件检索当前周期商机，未匹配到结果。"
                )
        elif query_type == "risk_progress":
            # Risk/progress focused query: skip KPI/detail retrieval.
            risk_type_codes = plan.get("risk_type_codes") if isinstance(plan, dict) else None
            force_opportunity_scope_all = False
            effective_intent = intent_for_execution.model_copy(deep=True)
            # M1 execution rule: prefer normalized query_plan scope for replay/audit.
            effective_intent.scope_type = (
                str(plan_scope_obj.get("type") or effective_intent.scope_type or "").strip()
                or None
            )
            effective_intent.scope_id = (
                str(plan_scope_obj.get("id") or effective_intent.scope_id or "").strip()
                or None
            )
            if not isinstance(risk_type_codes, list):
                risk_type_codes = None
            if not template_id and (effective_intent.scope_type or "").strip().lower() == "owner":
                owner_scope_id = (effective_intent.scope_id or "").strip()
                if owner_scope_id == "__CURRENT_USER__":
                    owner_scope_id = (current_owner_id or "").strip()
                if not owner_scope_id:
                    owner_name = str((detail_filters or {}).get("owner_name") or "").strip()
                    if owner_name:
                        owner_scope_id = self._resolve_owner_id_by_name(
                            db_session=db_session,
                            snapshot_period=snapshot_period,
                            owner_name=owner_name,
                        ) or ""
                        if not owner_scope_id:
                            ctx.query_note = (
                                "### 需确认销售人员\n"
                                f"- 暂未识别到“{owner_name}”对应的销售人员。"
                                "请确认姓名后重试，或改用“我负责的商机风险”进行查询。"
                            )
                            return ctx
                if owner_scope_id:
                    effective_intent.scope_id = owner_scope_id
            if template_id == "target_action_to_hit_goal":
                risk_type_codes = self._resolve_target_action_risk_type_codes(
                    db_session=db_session,
                    session_id=session_id,
                    department_id=review_session.department_id,
                )
            risk_universe_map = load_risk_type_name_map(db_session, None)
            if risk_type_codes:
                risk_type_codes, _ = validate_risk_type_codes(risk_type_codes, risk_universe_map)
            # For normal (non-template) risk questions:
            # - department/company scope -> business-risk relation chain
            # - opportunity/customer/owner scope -> opp risk-progress chain
            # - unspecified scope -> merge both chains
            if not template_id and not risk_type_codes:
                scope = str(
                    plan_scope_obj.get("type")
                    or effective_intent.scope_type
                    or ""
                ).strip().lower()
                if scope in ("department", "company"):
                    risk_type_codes = list(BUSINESS_RISK_TYPE_CODES)
                elif scope in ("opportunity", "customer", "owner"):
                    risk_type_codes = []
                    force_opportunity_scope_all = True
            if not risk_type_codes and template_id not in (
                "target_action_to_hit_goal",
                "focus_risky_opportunities",
                "opportunity_risk_taxonomy",
            ) and not force_opportunity_scope_all:
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
                    effective_intent,
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
                    top_solutions = self._collect_top_solutions(ctx.risks, limit=1)
                    action_line = (
                        f"- 可优先动作：{top_solutions[0]}。\n"
                        if top_solutions
                        else ""
                    )
                    ctx.query_note = (
                        "### 重点关注商机\n"
                        f"- 当前结论：共识别到 {opp_count} 个需重点关注的风险商机。\n"
                        f"- 涉及商机：{opp_list_text}。\n"
                        f"{action_line}"
                        "- 查看路径：点击商机评估Agent，筛选有风险的商机，点击商机详情。"
                    )
                else:
                    ctx.query_note = (
                        "### 重点关注商机\n"
                        "- 当前结论：暂未识别到风险商机。\n"
                        "- 建议动作：点击商机评估Agent，筛选有风险的商机，持续关注新增风险后再查看商机详情。"
                    )
                return ctx
            if template_id == "opportunity_risk_taxonomy":
                breakdown, note = self._build_opportunity_risk_taxonomy_breakdown(
                    db_session=db_session,
                    session_id=session_id,
                    snapshot_period=snapshot_period,
                    intent=intent,
                )
                ctx.risk_category_breakdown = breakdown
                ctx.risks = []
                ctx.progresses = []
                ctx.query_note = note
                return ctx
            if not template_id and force_opportunity_scope_all:
                risks_and_progress = self._query_risk_progress(
                    db_session, session_id, snapshot_period, effective_intent, risk_type_codes=None
                )
                ctx.risks = [
                    r for r in risks_and_progress if r.get("record_type") in ("RISK", "OPP_SUMMARY")
                ]
                card_hint = "商机风险卡片"
                opp_names: List[str] = []
                for r in ctx.risks:
                    name = (r.get("opportunity_name") or "").strip()
                    if name and name not in opp_names:
                        opp_names.append(name)
                opp_list_text = "、".join(opp_names) if opp_names else "（未解析到商机名称）"
                opp_count = len(opp_names) if opp_names else len(ctx.risks)
                if ctx.risks:
                    ctx.query_note = (
                        "### 风险查询结果\n"
                        f"- 当前结论：共识别到 {opp_count} 个风险商机。\n"
                        f"- 风险与对象：范围为商机/客户层面风险，涉及商机包括 {opp_list_text}。\n"
                        f"- 查看路径：进入经营洞察卡片，点击{card_hint}查看商机与风险详情。"
                    )
                else:
                    ctx.query_note = (
                        "### 风险查询结果\n"
                        f"- 当前结论：暂未识别到风险商机。\n"
                        f"- 建议动作：进入经营洞察卡片，点击{card_hint}持续关注新增风险。"
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
                    owner_id=effective_intent.scope_id if (effective_intent.scope_type or "").strip().lower() == "owner" else None,
                )
                card_hint = "达成风险卡片" if "业绩达成风险" in relation_type_names else "经营洞察卡片"
            elif opportunity_codes and not business_codes:
                risks_and_progress = self._query_risk_progress(
                    db_session, session_id, snapshot_period, effective_intent, risk_type_codes=opportunity_codes
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
                    owner_id=effective_intent.scope_id if (effective_intent.scope_type or "").strip().lower() == "owner" else None,
                ) if business_codes else []
                opp_risks = self._query_risk_progress(
                    db_session, session_id, snapshot_period, effective_intent, risk_type_codes=opportunity_codes
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
                if template_id == "target_action_to_hit_goal":
                    top_solutions = self._collect_top_solutions(ctx.risks, limit=3)
                    action_text = (
                        f"- 建议动作（优先执行）：{'；'.join(top_solutions)}。\n"
                        if top_solutions
                        else "- 建议动作：优先处理高风险商机并跟进关键阻塞点。\n"
                    )
                    ctx.query_note = (
                        "### 达成目标建议\n"
                        f"- 当前结论：共识别到 {opp_count} 个需要优先处理的风险商机。\n"
                        f"- 重点对象：涉及商机包括 {opp_list_text}。\n"
                        f"{action_text}"
                        f"{path_text}"
                    )
                else:
                    top_solutions = self._collect_top_solutions(ctx.risks, limit=1)
                    action_line = (
                        f"- 可优先动作：{top_solutions[0]}。\n"
                        if top_solutions
                        else ""
                    )
                    ctx.query_note = (
                        "### 达成风险查询结果\n"
                        f"- 当前结论：共识别到 {opp_count} 个达成风险商机。\n"
                        f"- 风险与对象：风险类型为{selected_risk_text}，涉及商机包括 {opp_list_text}。\n"
                        f"{action_line}"
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
                    owner_id=enforced_owner_id,
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
                owner_preview = self._build_owner_gap_preview(ctx.kpi_metrics)
                if owner_preview:
                    ctx.query_note = f"{ctx.query_note}\n\n{owner_preview}"
            else:
                ctx.kpi_metrics = self._query_kpi_metrics(
                    db_session, session_id, intent_for_execution
                )
            ctx.opportunity_snapshot_rows = self._collect_opportunity_snapshot_rows(
                db_session,
                review_session,
                intent_for_execution,
                user_question=question,
            )

        if query_type == "kpi_aggregation":
            ctx.snapshot_aggregations = self._query_snapshot_aggregations(
                db_session,
                snapshot_period,
                intent_for_execution,
                skip_opportunity_detail=bool(ctx.opportunity_snapshot_rows),
            )

        risks_and_progress = self._query_risk_progress(
            db_session, session_id, snapshot_period, intent_for_execution, risk_type_codes=None
        )
        ctx.risks = [
            r for r in risks_and_progress if r.get("record_type") in ("RISK", "OPP_SUMMARY")
        ]
        ctx.progresses = [
            r for r in risks_and_progress if r.get("record_type") == "PROGRESS"
        ]

        if intent_for_execution.time_comparison == "wow":
            ctx.comparison_data = self._build_wow_comparison(ctx.kpi_metrics)

        return ctx

    def _query_typical_opportunity_details(
        self,
        db_session: Session,
        snapshot_period: str,
        department_id: Optional[str],
        scope_owner_id: Optional[str],
        detail_filters: Dict[str, Any],
        current_owner_id: Optional[str] = None,
        current_owner_name: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        S = CRMReviewOppBranchSnapshot
        stmt = select(S).where(S.snapshot_period == snapshot_period)
        if department_id:
            stmt = stmt.where(S.owner_department_id == department_id)
        if scope_owner_id:
            stmt = stmt.where(S.owner_id == scope_owner_id)

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
            if scope_owner_id:
                stmt2 = stmt2.where(S.owner_id == scope_owner_id)
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
        owner_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        K = CRMReviewKpiMetrics
        stmt = select(K).where(
            K.session_id == session_id,
            K.scope_type == "owner",
            K.metric_name == "gap",
        )
        if owner_id:
            stmt = stmt.where(K.scope_id == owner_id)
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
        owner_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        RR = CRMReviewRiskOpportunityRelation
        stmt = select(RR).where(
            RR.session_id == session_id,
            RR.snapshot_period == snapshot_period,
        )
        if relation_type_names:
            stmt = stmt.where(RR.type_name.in_(relation_type_names))
        if owner_id:
            stmt = stmt.where(RR.owner_id == owner_id)
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

    @staticmethod
    def _resolve_owner_id_by_name(
        db_session: Session,
        snapshot_period: str,
        owner_name: str,
    ) -> Optional[str]:
        if not owner_name:
            return None
        S = CRMReviewOppBranchSnapshot
        row = db_session.exec(
            select(S).where(
                S.snapshot_period == snapshot_period,
                S.owner_name == owner_name,
            )
        ).first()
        if row and getattr(row, "owner_id", None):
            return str(row.owner_id)
        return None

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
        owner_id: Optional[str],
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
        if owner_id:
            stmt = stmt.where(S.owner_id == owner_id)
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
            if owner_id:
                stmt2 = stmt2.where(S.owner_id == owner_id)
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

    @staticmethod
    def _risk_taxonomy_ordered_names(
        opp_ids: set,
        id_to_display: Dict[str, str],
    ) -> List[str]:
        missing_oid = "__missing_opportunity_id__"
        names: List[str] = []
        seen: set[str] = set()
        for oid in opp_ids:
            if oid == missing_oid:
                disp = id_to_display.get(missing_oid, "未知商机")
            else:
                disp = (id_to_display.get(oid) or oid or "").strip()
            if not disp:
                continue
            if disp not in seen:
                seen.add(disp)
                names.append(disp)
        names.sort()
        return names

    @staticmethod
    def _collect_top_solutions(
        rows: List[Dict[str, Any]],
        limit: int = 3,
    ) -> List[str]:
        solutions: List[str] = []
        seen: set[str] = set()
        for row in rows:
            s = str(row.get("solution") or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            solutions.append(s)
            if len(solutions) >= limit:
                break
        return solutions

    def _build_opportunity_risk_taxonomy_breakdown(
        self,
        db_session: Session,
        session_id: str,
        snapshot_period: str,
        intent: ReviewIntent,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Aggregate RISK rows by crm_review_risk_category.category_group (+ per type_code)."""
        meta = load_risk_code_meta(db_session)
        rows = self._query_risk_progress(
            db_session,
            session_id,
            snapshot_period,
            intent,
            risk_type_codes=None,
        )
        risk_rows = [r for r in rows if r.get("record_type") == "RISK"]
        missing_oid = "__missing_opportunity_id__"
        cat_opp: Dict[str, set] = defaultdict(set)
        cat_type_opp: Dict[str, Dict[str, set]] = defaultdict(
            lambda: defaultdict(set)
        )
        cat_type_name: Dict[str, Dict[str, str]] = defaultdict(dict)
        cat_solutions: Dict[str, List[str]] = defaultdict(list)
        id_to_display: Dict[str, str] = {}

        for r in risk_rows:
            code_raw = str(r.get("type_code") or "").strip()
            code = code_raw or "__MISSING_TYPE_CODE__"
            row_meta = meta.get(code_raw) if code_raw else None
            if row_meta:
                cg = row_meta["category_group"]
                name_zh = row_meta["name_zh"]
            else:
                cg = "未在配置表命中"
                name_zh = (str(r.get("type_name") or "").strip() or code)
            oid = str(r.get("opportunity_id") or "").strip()
            if not oid:
                oid = missing_oid
            oname = (r.get("opportunity_name") or "").strip()
            display = oname or ("" if oid == missing_oid else oid)
            if oid == missing_oid:
                id_to_display.setdefault(missing_oid, "未知商机")
            elif display:
                id_to_display[oid] = display
            cat_opp[cg].add(oid)
            cat_type_opp[cg][code].add(oid)
            cat_type_name[cg][code] = name_zh
            solution = str(r.get("solution") or "").strip()
            if solution and solution not in cat_solutions[cg]:
                cat_solutions[cg].append(solution)

        breakdown: List[Dict[str, Any]] = []
        for cg, opp_ids in cat_opp.items():
            by_types: List[Dict[str, Any]] = []
            for tcode, oset in sorted(
                cat_type_opp[cg].items(),
                key=lambda x: (-len(x[1]), x[0]),
            ):
                by_types.append(
                    {
                        "type_code": tcode,
                        "name_zh": cat_type_name[cg].get(tcode, tcode),
                        "opportunity_count": len(oset),
                        "opportunity_names": self._risk_taxonomy_ordered_names(
                            oset, id_to_display
                        ),
                    }
                )
            breakdown.append(
                {
                    "category_group": cg,
                    "opportunity_count": len(opp_ids),
                    "opportunity_names": self._risk_taxonomy_ordered_names(
                        opp_ids, id_to_display
                    ),
                    "by_risk_type": by_types,
                }
            )

        breakdown.sort(
            key=lambda b: (-b["opportunity_count"], str(b["category_group"]))
        )

        path_line = (
            "- 查看路径：点击商机评估Agent，筛选有风险的商机，点击商机详情。"
        )
        if not breakdown:
            return (
                [],
                "### 商机风险分类统计\n"
                "- 当前结论：本期在商机风险进度中暂未识别到 RISK 记录。\n"
                "- 说明：风险类型范围以系统当前风险配置为准；以下为本期统计结果。\n"
                f"{path_line}",
            )

        all_opp_ids: set[str] = set()
        for s in cat_opp.values():
            all_opp_ids |= s
        total_opp = len(all_opp_ids)
        n_cat = len(breakdown)

        lines = [
            "### 商机风险分类统计",
            f"- 当前结论：按配置表风险类别汇总，本期共 {n_cat} 个类别出现风险命中，"
            f"涉及 {total_opp} 个不同商机。",
            "- 分类明细：",
        ]
        for b in breakdown:
            cg = b["category_group"]
            cnt = b["opportunity_count"]
            names = b["opportunity_names"]
            shown = names[:12]
            tail = f" 等共 {cnt} 个" if len(names) > len(shown) else ""
            lines.append(
                f"  - **{cg}**：{cnt} 个商机 — "
                f"{('、'.join(shown) if shown else '（无可用商机名称）')}{tail}"
            )
            if cat_solutions.get(cg):
                lines.append(f"    · 建议动作参考：{'；'.join(cat_solutions[cg][:2])}")
            for t in b.get("by_risk_type") or []:
                tcnt = t["opportunity_count"]
                tnames = t["opportunity_names"]
                tshown = tnames[:8]
                ttail = f" 等共 {tcnt} 个" if len(tnames) > len(tshown) else ""
                lines.append(
                    f"    · {t['name_zh']}（{t['type_code']}）：{tcnt} 个商机 — "
                    f"{('、'.join(tshown) if tshown else '（无可用商机名称）')}{ttail}"
                )
        lines.append(path_line)
        return breakdown, "\n".join(lines)

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
