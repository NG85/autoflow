"""Markdown formatters for review data — converts structured review rows into
rich-text documents suitable for vector embedding and knowledge graph extraction.

Each function returns a list of markdown lines, following the same pattern
established in ``crm_format.py``.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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

    session_name = getattr(session, "session_name", None) or getattr(session, "unique_id", "")
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
            scope_name = getattr(m, "scope_name", "") or getattr(m, "scope_id", "")
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

    Parameters
    ----------
    snapshot : CRMReviewOppBranchSnapshot
    """
    lines: List[str] = []

    opp_name = getattr(snapshot, "opportunity_name", None) or getattr(snapshot, "opportunity_id", "")
    lines.append(f"# 商机快照: {opp_name}")
    lines.append("")
    lines.append(f"- **商机ID**: {getattr(snapshot, 'opportunity_id', '')}")
    lines.append(f"- **客户**: {getattr(snapshot, 'account_name', '') or ''}")
    lines.append(f"- **负责人**: {getattr(snapshot, 'owner_name', '') or ''}")
    lines.append(f"- **负责人部门**: {getattr(snapshot, 'owner_department_name', '') or ''}")
    lines.append(f"- **快照周期**: {getattr(snapshot, 'snapshot_period', '')}")
    lines.append("")

    # Baseline data (T2 frozen) — the authoritative values used for report analysis
    baseline_forecast = getattr(snapshot, "baseline_forecast_type", None)
    baseline_amount = getattr(snapshot, "baseline_forecast_amount", None)
    baseline_stage = getattr(snapshot, "baseline_opportunity_stage", None)
    lines.append("## 报告基线数据（T2 冻结，报告分析依据）")
    lines.append("")
    lines.append(f"- **预测状态**: {baseline_forecast or getattr(snapshot, 'forecast_type', '') or ''}")
    lines.append(f"- **签约金额**: {_fmt(baseline_amount if baseline_amount is not None else getattr(snapshot, 'forecast_amount', None))}")
    lines.append(f"- **商机阶段**: {baseline_stage or getattr(snapshot, 'opportunity_stage', '') or ''}")
    lines.append(f"- **预计成交日期**: {getattr(snapshot, 'baseline_expected_closing_date', '') or getattr(snapshot, 'expected_closing_date', '') or ''}")
    lines.append(f"- **阶段停留天数**: {_fmt(getattr(snapshot, 'stage_stay', 0))}")
    lines.append("")

    # Current values (latest, may have been modified by user after T2)
    lines.append("## 最新预测数据（用户可修改）")
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
        lines.append("## AI 评估")
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
    was_changed = getattr(snapshot, "was_changed_to_commit", False)
    was_modified = getattr(snapshot, "was_modified", False)
    if was_changed or was_modified:
        lines.append("## 变更记录")
        lines.append("")
        if was_changed:
            lines.append("- 已调整为 Commit")
        if was_modified:
            lines.append(f"- 已被修改 (修改人: {getattr(snapshot, 'last_modified_by', '') or ''})")
        lines.append("")

    return lines


def format_risk_progress_info(
    risk_progress,
    opportunity_name: Optional[str] = None,
    scope_name: Optional[str] = None,
) -> List[str]:
    """Format a single risk/progress record into markdown lines.

    Parameters
    ----------
    risk_progress : CRMReviewOppRiskProgress
    opportunity_name : str | None
        Resolved opportunity display name.  Falls back to ``opportunity_id``
        when *None*.
    scope_name : str | None
        Resolved display name for *scope_id* (opportunity name, owner name,
        or department name depending on *scope_type*).  Falls back to
        ``scope_id`` when *None*.
    """
    lines: List[str] = []

    record_type = getattr(risk_progress, "record_type", "")
    type_name = getattr(risk_progress, "type_name", "")
    type_label = "风险" if record_type == "RISK" else "进展" if record_type == "PROGRESS" else "商机总结" if record_type == "OPP_SUMMARY" else record_type
    lines.append(f"# {type_label}: {type_name}")
    lines.append("")

    lines.append(f"- **记录类型**: {record_type}")
    lines.append(f"- **类型代码**: {getattr(risk_progress, 'type_code', '')}")
    scope_display = scope_name or getattr(risk_progress, 'scope_id', '') or '全局'
    lines.append(f"- **范围**: {getattr(risk_progress, 'scope_type', '')} / {scope_display}")
    lines.append(f"- **快照周期**: {getattr(risk_progress, 'snapshot_period', '')}")
    lines.append(f"- **计算阶段**: {getattr(risk_progress, 'calc_phase', '')}")
    lines.append("")

    opp_name = opportunity_name or getattr(risk_progress, "opportunity_name", None)
    opp_id = getattr(risk_progress, "opportunity_id", None)
    if opp_name or opp_id:
        lines.append(f"- **关联商机**: {opp_name or opp_id}")

    severity = getattr(risk_progress, "severity", None)
    if severity:
        lines.append(f"- **严重程度**: {severity}")

    category = getattr(risk_progress, "category", None)
    if category:
        lines.append(f"- **分类**: {category}")

    source = getattr(risk_progress, "source", None)
    if source:
        lines.append(f"- **来源**: {source}")

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

    # Quantitative fields
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

    status = getattr(risk_progress, "status", None)
    if status:
        lines.append(f"- **状态**: {status}")
        lines.append("")

    return lines
