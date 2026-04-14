"""Markdown formatters for review data — converts structured review rows into
rich-text documents suitable for vector embedding and knowledge graph extraction.

Each function returns a list of markdown lines, following the same pattern
established in ``crm_format.py``.
"""

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ``CRMReviewOppRiskProgress.scope_type``: granularity of the row; ``scope_id``
# points at the corresponding entity (opportunity / owner / dept). ``company``
# means whole-company scope with no ``scope_id``.
_SCOPE_TYPE_ZH: Dict[str, str] = {
    "opportunity": "商机",
    "owner": "负责人",
    "department": "部门",
    "company": "公司",
}


def _scope_type_zh(scope_type: str) -> str:
    key = (scope_type or "").strip().lower()
    return _SCOPE_TYPE_ZH.get(key, scope_type or "")


def _fmt(value: Any) -> str:
    """Safe string conversion that handles None, Decimal, date/datetime."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def format_review_session_info(
    session,
    kpi_metrics: Optional[List] = None,
) -> List[str]:
    """Format a review session with its KPI metrics into markdown lines.

    Parameters
    ----------
    session : CRMReviewSession
    kpi_metrics : list[CRMReviewKpiMetrics] | None
    """
    lines: List[str] = []

    session_name = (
        getattr(session, "session_name", None)
        or getattr(session, "department_name", None)
        or getattr(session, "period", "")
        or "（Review 会话）"
    )
    dept_name = getattr(session, "department_name", "") or ""
    period = getattr(session, "period", "")
    stage = getattr(session, "stage", "")

    lines.append(f"# Review Session: {session_name}")
    lines.append("")
    lines.append(f"- **部门**: {dept_name}")
    lines.append(f"- **周期**: {period}")
    lines.append(f"- **周期类型**: {_fmt(getattr(session, 'period_type', ''))}")
    lines.append(f"- **周期起止**: {_fmt(getattr(session, 'period_start', ''))} ~ {_fmt(getattr(session, 'period_end', ''))}")
    lines.append(f"- **阶段**: {stage}")
    lines.append(f"- **Review 类型**: {_fmt(getattr(session, 'review_type', ''))}")
    lines.append(f"- **财年**: {_fmt(getattr(session, 'fiscal_year', ''))}")
    lines.append(f"- **报告日期**: {_fmt(getattr(session, 'report_date', ''))}")
    lines.append("")

    if kpi_metrics:
        lines.append("## 关键指标 (KPI)")
        lines.append("")
        lines.append("| 范围 | 指标分类 | 指标名称 | 当前值 | 上期值 | 变化量 | 变化率 | 单位 |")
        lines.append("|------|----------|----------|--------|--------|--------|--------|------|")
        for m in kpi_metrics:
            scope_name = getattr(m, "scope_name", "") or ""
            rate_str = ""
            rate_val = getattr(m, "metric_rate", None)
            if rate_val is not None:
                rate_str = f"{float(rate_val) * 100:.1f}%"
            lines.append(
                f"| {scope_name} "
                f"| {getattr(m, 'metric_category', '')} "
                f"| {getattr(m, 'metric_name', '')} "
                f"| {_fmt(getattr(m, 'metric_value', None))} "
                f"| {_fmt(getattr(m, 'metric_value_prev', None))} "
                f"| {_fmt(getattr(m, 'metric_delta', None))} "
                f"| {rate_str} "
                f"| {getattr(m, 'metric_unit', '') or ''} |"
            )
        lines.append("")

    return lines


def format_snapshot_info(snapshot) -> List[str]:
    """Format a single opportunity branch snapshot into markdown lines.

    CRM IDs are omitted from the body; use names for embedding.  Resolve
    ``opportunity_name`` upstream when possible — otherwise a neutral title
    placeholder is used (IDs remain on indexed ``Document.meta``).

    Parameters
    ----------
    snapshot : CRMReviewOppBranchSnapshot
    """
    lines: List[str] = []

    opp_display = getattr(snapshot, "opportunity_name", None) or "（商机名称未提供）"
    lines.append(f"# 商机快照: {opp_display}")
    lines.append("")
    lines.append(f"- **客户**: {getattr(snapshot, 'account_name', '') or ''}")
    lines.append(f"- **负责人**: {getattr(snapshot, 'owner_name', '') or ''}")
    lines.append(f"- **负责人部门**: {getattr(snapshot, 'owner_department_name', '') or ''}")
    lines.append(f"- **快照周期**: {getattr(snapshot, 'snapshot_period', '')}")
    lines.append("")

    # Baseline data (T2 frozen) — the authoritative values used for report analysis
    baseline_forecast = getattr(snapshot, "baseline_forecast_type", None)
    baseline_amount = getattr(snapshot, "baseline_forecast_amount", None)
    baseline_stage = getattr(snapshot, "baseline_opportunity_stage", None)
    lines.append("## 报告基线数据（报告分析依据）")
    lines.append("")
    lines.append(f"- **预测状态**: {baseline_forecast or getattr(snapshot, 'forecast_type', '') or ''}")
    lines.append(f"- **签约金额**: {_fmt(baseline_amount if baseline_amount is not None else getattr(snapshot, 'forecast_amount', None))}")
    lines.append(f"- **商机阶段**: {baseline_stage or getattr(snapshot, 'opportunity_stage', '') or ''}")
    lines.append(f"- **预计成交日期**: {getattr(snapshot, 'baseline_expected_closing_date', '') or getattr(snapshot, 'expected_closing_date', '') or ''}")
    lines.append(f"- **阶段停留天数**: {_fmt(getattr(snapshot, 'stage_stay', 0))}")
    lines.append("")

    # Current values (latest, may have been modified by user after T2)
    lines.append("## 最新预测数据（销售可修改）")
    lines.append("")
    lines.append(f"- **预测状态**: {getattr(snapshot, 'forecast_type', '') or ''}")
    lines.append(f"- **签约金额**: {_fmt(getattr(snapshot, 'forecast_amount', None))}")
    lines.append(f"- **商机阶段**: {getattr(snapshot, 'opportunity_stage', '') or ''}")
    lines.append(f"- **预计成交日期**: {getattr(snapshot, 'expected_closing_date', '') or ''}")
    lines.append("")

    # AI evaluations
    ai_commit = getattr(snapshot, "ai_commit", None)
    ai_stage = getattr(snapshot, "ai_stage", None)
    if ai_commit or ai_stage:
        lines.append("## AI 评估（AI给出的判断结果）")
        lines.append("")
        if ai_commit:
            lines.append(f"- **AI 判断的预测状态**: {ai_commit}")
        if ai_stage:
            lines.append(f"- **AI 判断的商机阶段**: {ai_stage}")
        ai_close = getattr(snapshot, "ai_expected_closing_date", None)
        if ai_close:
            lines.append(f"- **AI 判断的预计成交日期**: {ai_close}")
        lines.append("")

    # Change tracking
    was_modified = getattr(snapshot, "was_modified", False)
    # Optional[int] may be NULL from DB; avoid TypeError on comparison.
    modification_count = getattr(snapshot, "modification_count", 0) or 0
    if was_modified or modification_count > 0:
        lines.append("## 变更记录")
        lines.append("")
        lines.append(f"- 最后修改时间: {_fmt(getattr(snapshot, 'update_time', None))}")
        lines.append(f"- 最后修改人: {getattr(snapshot, 'last_modified_by', '')}")
        lines.append(f"- 修改次数: {modification_count}")
        lines.append("")

    return lines


def format_risk_progress_info(
    risk_progress,
    opportunity_name: Optional[str] = None,
    scope_name: Optional[str] = None,
    owner_name: Optional[str] = None,
    department_name: Optional[str] = None,
) -> List[str]:
    """Format a single risk/progress record into markdown lines.

    Semantics (``CRMReviewOppRiskProgress``): *record_type* distinguishes a risk
    row, a progress row, or an opportunity-summary row. *category* is the broad
    class; *type_name* names the specific subtype under that class (codes are
    not embedded in body text).

    Raw UUID/CRM IDs are omitted from the body text (kept on ``Document.meta``
    at index time) so embeddings emphasize human-readable names.

    Field groups: type hierarchy, scope attributes, assessments, narrative
    fields, metrics, lifecycle, audit.

    Parameters
    ----------
    risk_progress : CRMReviewOppRiskProgress
    opportunity_name : str | None
        Resolved opportunity display name.  When *None*, the association line
        is omitted (IDs are not embedded).
    scope_name : str | None
        Resolved display name for *scope_id*. *scope_type* is one of
        ``opportunity`` (商机), ``owner`` (负责人), ``department`` (部门),
        ``company`` (公司 — no *scope_id*).  Raw *scope_id* is not embedded.
    owner_name : str | None
        Resolved owner display name for *owner_id* when the row is opp-scoped.
    department_name : str | None
        Resolved department display name for *department_id* when known.
    """
    lines: List[str] = []

    record_type = getattr(risk_progress, "record_type", "") or ""
    type_name = getattr(risk_progress, "type_name", "") or ""
    type_label = (
        "风险"
        if record_type == "RISK"
        else "进展"
        if record_type == "PROGRESS"
        else "商机总结"
        if record_type == "OPP_SUMMARY"
        else record_type
    )

    lines.append(f"# {type_label}: {type_name}")
    lines.append("")

    category = getattr(risk_progress, "category", None)

    # --- Scope & type hierarchy (no raw DB/CRM IDs in body — names only) ---
    lines.append("## 上下文")
    lines.append("")
    lines.append(f"- **记录性质**: {type_label}（`{record_type}`）")
    if category:
        lines.append(f"- **大类**: {category}")
    lines.append(f"- **具体类型**: {type_name}")
    st = getattr(risk_progress, "scope_type", "") or ""
    st_key = st.strip().lower()
    scope_id_raw = getattr(risk_progress, "scope_id", None)
    st_zh = _scope_type_zh(st)
    if st_key == "company":
        scope_tail = scope_name or "全公司"
    elif scope_name:
        scope_tail = scope_name
    elif not scope_id_raw:
        scope_tail = "全局"
    else:
        scope_tail = "范围名称未解析"
    if st:
        lines.append(f"- **数据范围**: {st_zh}（`{st}`）/ {scope_tail}")
    else:
        lines.append(f"- **数据范围**: {scope_tail}")
    if department_name:
        lines.append(f"- **部门**: {department_name}")
    opp_name = opportunity_name or getattr(risk_progress, "opportunity_name", None)
    if opp_name:
        lines.append(f"- **关联商机**: {opp_name}")
    if owner_name:
        lines.append(f"- **负责人**: {owner_name}")
    lines.append(f"- **快照周期**: {getattr(risk_progress, 'snapshot_period', '')}")
    lines.append(f"- **计算阶段**: {getattr(risk_progress, 'calc_phase', '')}")
    lines.append("")

    # --- BRD dimensions (category is under 上下文 as 大类) ---
    level = getattr(risk_progress, "level", None)
    severity = getattr(risk_progress, "severity", None)
    source = getattr(risk_progress, "source", None)
    metric_name = getattr(risk_progress, "metric_name", None)
    brd_lines: List[str] = []
    if level:
        brd_lines.append(f"- **层级**: {level}")
    if severity:
        brd_lines.append(f"- **严重程度**: {severity}")
    if source:
        brd_lines.append(f"- **来源**: {source}")
    if metric_name:
        brd_lines.append(f"- **指标**: {metric_name}")
    if brd_lines:
        lines.append("## 分层与属性")
        lines.append("")
        lines.extend(brd_lines)
        lines.append("")

    # --- AI / sales assessments & rule ---
    ai_asst = getattr(risk_progress, "ai_assessment", None)
    sales_asst = getattr(risk_progress, "sales_assessment", None)
    if ai_asst or sales_asst:
        lines.append("## 评估")
        lines.append("")
        if ai_asst:
            lines.append(f"- **AI 评估**: {ai_asst}")
        if sales_asst:
            lines.append(f"- **销售填报**: {sales_asst}")
        lines.append("")

    judgment_rule = getattr(risk_progress, "judgment_rule", None)
    if judgment_rule:
        lines.append("## 风险判断规则")
        lines.append("")
        lines.append(judgment_rule)
        lines.append("")

    summary = getattr(risk_progress, "summary", None)
    if summary:
        lines.append("## 概述")
        lines.append("")
        lines.append(summary)
        lines.append("")

    gap_desc = getattr(risk_progress, "gap_description", None)
    if gap_desc:
        lines.append("## GAP 描述")
        lines.append("")
        lines.append(gap_desc)
        lines.append("")

    detail = getattr(risk_progress, "detail_description", None)
    if detail:
        lines.append("## 详情")
        lines.append("")
        lines.append(detail)
        lines.append("")

    solution = getattr(risk_progress, "solution", None)
    if solution:
        lines.append("## 解决建议")
        lines.append("")
        lines.append(solution)
        lines.append("")

    evidence = getattr(risk_progress, "evidence", None)
    if evidence:
        lines.append("## 依据数据")
        lines.append("")
        try:
            lines.append(json.dumps(evidence, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            lines.append(str(evidence))
        lines.append("")

    fin_impact = getattr(risk_progress, "financial_impact", None)
    prev_val = getattr(risk_progress, "previous_value", None)
    cur_val = getattr(risk_progress, "current_value", None)
    roc = getattr(risk_progress, "rate_of_change", None)
    if any(v is not None for v in [fin_impact, prev_val, cur_val, roc]):
        lines.append("## 量化指标")
        lines.append("")
        if fin_impact is not None:
            lines.append(f"- **财务影响**: {_fmt(fin_impact)}")
        if prev_val is not None:
            lines.append(f"- **上期值**: {_fmt(prev_val)}")
        if cur_val is not None:
            lines.append(f"- **当期值**: {_fmt(cur_val)}")
        if roc is not None:
            lines.append(f"- **变化率**: {float(roc) * 100:.1f}%")
        lines.append("")

    lines.append("## 状态与闭环")
    lines.append("")
    lines.append(f"- **状态**: {getattr(risk_progress, 'status', None) or ''}")
    lines.append(f"- **检出时间**: {_fmt(getattr(risk_progress, 'detected_at', None))}")
    lines.append(f"- **解决时间**: {_fmt(getattr(risk_progress, 'resolved_at', None))}")
    resolved_by = getattr(risk_progress, "resolved_by", None)
    if resolved_by:
        lines.append(f"- **解决人**: {resolved_by}")
    resolution_type = getattr(risk_progress, "resolution_type", None)
    if resolution_type:
        lines.append(f"- **解决方式**: {resolution_type}")
    resolution_note = getattr(risk_progress, "resolution_note", None)
    if resolution_note:
        lines.append(f"- **解决说明**: {resolution_note}")
    lines.append("")

    lines.append("## 记录审计")
    lines.append("")
    lines.append(f"- **创建时间**: {_fmt(getattr(risk_progress, 'created_at', None))}")
    lines.append(f"- **更新时间**: {_fmt(getattr(risk_progress, 'updated_at', None))}")
    lines.append(f"- **创建人**: {getattr(risk_progress, 'created_by', '') or ''}")
    lines.append(f"- **更新人**: {getattr(risk_progress, 'updated_by', '') or ''}")
    lines.append("")

    meta = getattr(risk_progress, "metadata_", None)
    if meta:
        lines.append("## 扩展元数据")
        lines.append("")
        try:
            lines.append(json.dumps(meta, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            lines.append(str(meta))
        lines.append("")

    return lines
