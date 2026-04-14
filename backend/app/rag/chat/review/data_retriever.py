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
    CRMReviewSession,
)
from app.rag.chat.review.intent_router import ReviewIntent

logger = logging.getLogger(__name__)

METRIC_DISPLAY_NAMES = {
    "opp_count": "商机数",
    "target": "目标",
    "closed": "已成单",
    "gap": "差额",
    "pipeline_coverage": "倍数",
    "commit_sales": "销售确定下单",
    "commit_ai": "AI确定下单",
    "upside_sales": "销售可能下单",
}

AMOUNT_METRICS = {"target", "closed", "gap", "commit_sales", "commit_ai", "upside_sales"}

MISMATCH_QUERY_CONFIGS = {
    "stage": {
        "aliases": ("阶段", "stage", "项目阶段", "商机阶段", "推进阶段"),
        "sales_field": "opportunity_stage",
        "ai_field": "ai_stage",
        "sales_label": "销售商机阶段",
        "ai_label": "AI商机阶段",
    },
    "forecast": {
        "aliases": ("预测", "判断", "commit", "forecast", "预测状态", "销售判断", "ai判断", "预测类型", "判断口径"),
        "sales_field": "forecast_type",
        "ai_field": "ai_commit",
        "sales_label": "销售预测状态",
        "ai_label": "AI预测状态",
    },
    "close_date": {
        "aliases": ("预计成交", "成交日期", "close date", "closing date", "成交时间", "关单时间"),
        "sales_field": "expected_closing_date",
        "ai_field": "ai_expected_closing_date",
        "sales_label": "销售预计成交日期",
        "ai_label": "AI预计成交日期",
    },
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
    if not question:
        return None
    q = question.lower()
    has_ai = ("ai" in q) or ("智能" in q) or ("算法" in q)
    has_sales = ("销售" in q) or ("业务" in q)
    has_diff = any(k in q for k in ("不同", "不一致", "差异", "不一样", "冲突"))
    has_opportunity = any(k in q for k in ("商机", "项目", "pipeline", "机会"))
    # 允许非“列表”问法，例如“有没有/是否存在/多少个不一致商机”
    has_target_shape = any(
        k in q for k in ("哪些", "列表", "清单", "列出", "有没有", "是否有", "是否存在", "多少", "几个", "数量")
    )
    if not (has_ai and (has_sales or has_opportunity) and has_diff):
        return None
    if not (has_target_shape or has_opportunity):
        return None
    for diff_type, cfg in MISMATCH_QUERY_CONFIGS.items():
        if any(alias in q for alias in cfg["aliases"]):
            return diff_type
    # 默认回退到阶段差异，避免漏掉“AI与销售判断不同”但未显式说维度的高频问法
    return "stage"


def _is_count_query(question: str) -> bool:
    if not question:
        return False
    q = question.lower()
    return any(k in q for k in ("多少", "几个", "数量", "count", "总数"))


def _is_list_query(question: str) -> bool:
    if not question:
        return False
    q = question.lower()
    return any(k in q for k in ("哪些", "列表", "清单", "列出", "明细", "list"))


def _is_general_opportunity_list_query(question: str) -> bool:
    if not question:
        return False
    q = question.lower()
    has_opportunity = any(k in q for k in ("商机", "项目", "pipeline", "机会"))
    has_list = _is_list_query(q) or any(k in q for k in ("有哪些", "给我看"))
    return has_opportunity and has_list


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
                    f"  预测: {row.get('forecast_type') or ''} / "
                    f"金额={row.get('forecast_amount', 'N/A')} | "
                    f"销售阶段: {row.get('opportunity_stage') or ''} / AI阶段: {row.get('ai_stage') or ''} | "
                    f"销售预计成交: {row.get('expected_closing_date') or ''} / "
                    f"AI预计成交: {row.get('ai_expected_closing_date') or ''}"
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

        if self.progresses:
            parts.append("\n### Progress Signals")
            for p in self.progresses:
                parts.append(
                    f"- {p.get('type_name', p.get('type_code', ''))}: "
                    f"{p.get('summary', p.get('detail_description', 'N/A'))}"
                )

        return "\n".join(parts) if parts else "(No risk/progress signals)"


class ReviewDataRetriever:
    """Retrieves structured data from CRM review tables based on intent parameters."""

    def retrieve(
        self,
        db_session: Session,
        review_session: CRMReviewSession,
        intent: ReviewIntent,
        user_question: Optional[str] = None,
    ) -> ReviewDataContext:
        ctx = ReviewDataContext()

        session_id = review_session.unique_id
        snapshot_period = review_session.period
        question = user_question or ""
        mismatch_type = _detect_mismatch_query_type(question)
        # 意图协同：若文本没有命中，但意图指向 sales/ai 预测对比，推断为 forecast 差异
        if not mismatch_type and set(intent.metric_names or []).intersection({"commit_sales", "commit_ai"}):
            mismatch_type = "forecast"

        if mismatch_type:
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
            )
            if _is_count_query(question) and not _is_list_query(question):
                ctx.query_note = (
                    f"### 差异计数结果\n- 按“{cfg['sales_label']} vs {cfg['ai_label']}”检索，"
                    f"不一致商机数量为 {len(ctx.opportunity_snapshot_rows)}。"
                )
            else:
                ctx.query_note = (
                    f"### 差异查询结果\n- 按“{cfg['sales_label']} vs {cfg['ai_label']}”检索，"
                    f"共找到 {len(ctx.opportunity_snapshot_rows)} 个不一致商机。"
                    if ctx.opportunity_snapshot_rows
                    else f"### 差异查询结果\n- 按“{cfg['sales_label']} vs {cfg['ai_label']}”检索，未发现不一致商机。"
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
            # 非特定名称但明确要“商机列表”时，返回周期内样本明细，避免误走聚合导致“无明细可答”
            if not ctx.opportunity_snapshot_rows and _is_general_opportunity_list_query(question):
                ctx.opportunity_snapshot_rows = self._query_period_opportunity_rows(
                    db_session=db_session,
                    snapshot_period=snapshot_period,
                    department_id=review_session.department_id,
                    limit=80,
                )
                ctx.query_note = (
                    f"### 商机列表结果\n- 已返回当前周期商机明细样本 {len(ctx.opportunity_snapshot_rows)} 条。"
                    if ctx.opportunity_snapshot_rows
                    else "### 商机列表结果\n- 当前周期未检索到可展示的商机明细。"
                )

            ctx.snapshot_aggregations = self._query_snapshot_aggregations(
                db_session,
                snapshot_period,
                intent,
                skip_opportunity_detail=bool(ctx.opportunity_snapshot_rows),
            )

        risks_and_progress = self._query_risk_progress(
            db_session, session_id, snapshot_period, intent
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
        rows = list(db_session.exec(stmt.order_by(S.opportunity_name).limit(limit)).all())
        if not rows and department_id:
            rows = list(
                db_session.exec(
                    select(S)
                    .where(
                        S.snapshot_period == snapshot_period,
                        sales_col.is_not(None),
                        ai_col.is_not(None),
                        sales_col != ai_col,
                    )
                    .order_by(S.opportunity_name)
                    .limit(limit)
                ).all()
            )

        output: List[Dict[str, Any]] = []
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
            output.append(row)
        return output

    def _query_period_opportunity_rows(
        self,
        db_session: Session,
        snapshot_period: str,
        department_id: Optional[str],
        limit: int = 80,
    ) -> List[Dict[str, Any]]:
        S = CRMReviewOppBranchSnapshot
        stmt = select(S).where(S.snapshot_period == snapshot_period)
        if department_id:
            stmt = stmt.where(S.owner_department_id == department_id)
        rows = list(db_session.exec(stmt.order_by(S.opportunity_name).limit(limit)).all())
        if not rows and department_id:
            rows = list(
                db_session.exec(
                    select(S).where(S.snapshot_period == snapshot_period).order_by(S.opportunity_name).limit(limit)
                ).all()
            )
        return [self._snapshot_row_to_detail(r) for r in rows]

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

        stmt = stmt.order_by(R.record_type, R.type_code)

        rows = db_session.exec(stmt).all()
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
