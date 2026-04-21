"""Markdown formatters for review data — converts structured review rows into
rich-text documents suitable for vector embedding and knowledge graph extraction.

Each function returns a list of markdown lines, following the same pattern
established in ``crm_format.py``.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from app.rag.chat.review.metric_catalog import METRIC_DISPLAY_NAMES

logger = logging.getLogger(__name__)


# Formatting guardrails for review markdown:
# - Keep body text focused on business semantics that users naturally ask about
#   and KG extraction can reliably turn into entities/relations.
# - Keep system/governance/control fields in Document.meta (filtering/routing),
#   instead of embedding them into body text.
# - Typical meta-only fields: calc phase, task/runtime status, raw IDs, audit
#   payloads, and low-level debug metadata.
# - When uncertain, prefer less noisy body text and add explicit structured
#   relations/metadata in the graph builder.

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


def _kv(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


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
    lines.append(f"- **周期起止**: {_fmt(getattr(session, 'period_start', ''))} ~ {_fmt(getattr(session, 'period_end', ''))}")
    lines.append(f"- **阶段**: {stage}")
    lines.append(f"- **Review 类型**: {_fmt(getattr(session, 'review_type', ''))}")
    lines.append(f"- **财年**: {_fmt(getattr(session, 'fiscal_year', ''))}")
    lines.append(f"- **报告日期**: {_fmt(getattr(session, 'report_date', ''))}")
    lines.append("")

    if kpi_metrics:
        lines.append("## 关键指标 (KPI)")
        lines.append("")
        dept_rows = [m for m in kpi_metrics if _kv(m, "scope_type", "") == "department"]
        owner_rows = [m for m in kpi_metrics if _kv(m, "scope_type", "") == "owner"]
        metric_order = [
            "opp_count",
            "target",
            "closed",
            "gap",
            "pipeline_coverage",
            "commit_sales",
            "commit_ai",
            "upside_sales",
        ]
        metric_headers = [METRIC_DISPLAY_NAMES.get(m, m) for m in metric_order]

        def _append_scope_matrix(title: str, rows: List[Any]) -> None:
            if not rows:
                return
            scope_matrix: Dict[str, Dict[str, Any]] = {}
            for m in rows:
                scope_type = _scope_type_zh(_kv(m, "scope_type", "") or "")
                scope_name = _kv(m, "scope_name", "") or _kv(m, "scope_id", "") or ""
                scope_key = f"{scope_type}::{scope_name}"
                metric_name = _kv(m, "metric_name", "") or ""
                if scope_key not in scope_matrix:
                    scope_matrix[scope_key] = {
                        "scope_type": scope_type,
                        "scope_name": scope_name,
                        "metrics": {},
                    }
                scope_matrix[scope_key]["metrics"][metric_name] = m

            lines.append(f"### {title}")
            lines.append("")
            sorted_scopes = sorted(
                scope_matrix.values(),
                key=lambda s: (str(s.get("scope_type", "")), str(s.get("scope_name", ""))),
            )

            # KG / Vector retrieval friendly factual sentences.
            # Keep one metric per line with explicit current/prev/delta/rate semantics.
            lines.append("#### KG/Vector Facts")
            lines.append("")
            lines.append(f"- 事实维度：周期={period or ''}；范围分组={title}")
            for scope in sorted_scopes:
                scope_type = scope["scope_type"]
                scope_name = scope["scope_name"]
                scope_kv_key = "部门" if scope_type == "部门" else "负责销售" if scope_type == "负责人" else "范围"
                for metric_name in metric_order:
                    m = scope["metrics"].get(metric_name)
                    metric_display = METRIC_DISPLAY_NAMES.get(metric_name, metric_name)
                    if not m:
                        continue
                    metric_value = _fmt(_kv(m, "metric_value", None))
                    metric_prev = _fmt(_kv(m, "metric_value_prev", None))
                    metric_delta = _fmt(_kv(m, "metric_delta", None))
                    rate_str = ""
                    rate_val = _kv(m, "metric_rate", None)
                    if rate_val is not None:
                        rate_str = f"{float(rate_val) * 100:.1f}%"
                    lines.append(
                        f"- [{title}] {scope_kv_key}={scope_name}；指标={metric_display} ({metric_name})；当前值={metric_value or '缺失'}；上期值={metric_prev or '缺失'}；变化量={metric_delta or '缺失'}；变化率={rate_str or '缺失'}。"
                    )
            lines.append("")

        _append_scope_matrix("部门范围", dept_rows)
        _append_scope_matrix("负责人范围（商机负责销售，属本期参会人）", owner_rows)

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
    lines.append(f"- **负责人（商机负责销售，属本期参会人）**: {getattr(snapshot, 'owner_name', '') or ''}")
    lines.append(f"- **负责人部门**: {getattr(snapshot, 'owner_department_name', '') or ''}")
    lines.append(f"- **快照周期**: {getattr(snapshot, 'snapshot_period', '')}")
    lines.append("")

    # Baseline data (T2 frozen) — the authoritative values used for report analysis
    baseline_forecast = getattr(snapshot, "baseline_forecast_type", None)
    baseline_amount = getattr(snapshot, "baseline_forecast_amount", None)
    baseline_stage = getattr(snapshot, "baseline_opportunity_stage", None)
    baseline_close_date = getattr(snapshot, "baseline_expected_closing_date", None)
    current_forecast = getattr(snapshot, "forecast_type", "") or ""
    current_amount = getattr(snapshot, "forecast_amount", None)
    current_stage = getattr(snapshot, "opportunity_stage", "") or ""
    current_close_date = getattr(snapshot, "expected_closing_date", "") or ""

    forecast_val = baseline_forecast if baseline_forecast not in (None, "") else current_forecast
    amount_val = baseline_amount if baseline_amount is not None else current_amount
    stage_val = baseline_stage if baseline_stage not in (None, "") else current_stage
    close_date_val = baseline_close_date if baseline_close_date not in (None, "") else current_close_date

    ai_commit = getattr(snapshot, "ai_commit", None)
    ai_stage = getattr(snapshot, "ai_stage", None)
    ai_close = getattr(snapshot, "ai_expected_closing_date", None)

    has_ai_commit = ai_commit not in (None, "")
    has_ai_stage = ai_stage not in (None, "")
    has_ai_close = ai_close not in (None, "")
    has_any_ai = has_ai_commit or has_ai_stage or has_ai_close

    lines.append("## 销售判断与AI判断" if has_any_ai else "## 销售判断")
    lines.append("")
    lines.append(f"- 销售判断 - 预测状态={forecast_val or '缺失'}")
    lines.append(f"- 销售判断 - 签约金额={_fmt(amount_val) or '缺失'}（仅销售字段，AI不做判断）")
    lines.append(f"- 销售判断 - 商机阶段={stage_val or '缺失'}")
    lines.append(f"- 销售判断 - 预计成交日期={close_date_val or '缺失'}")
    lines.append(
        f"- 销售判断 - 阶段停留天数={_fmt(getattr(snapshot, 'stage_stay', None)) or '缺失'}（仅销售字段，AI不做判断）"
    )
    lines.append("")

    if has_ai_commit or has_ai_stage or has_ai_close:
        if has_ai_commit:
            lines.append("- AI判断 - 预测状态=" + ai_commit)
        if has_ai_stage:
            lines.append("- AI判断 - 商机阶段=" + ai_stage)
        if has_ai_close:
            lines.append("- AI判断 - 预计成交日期=" + ai_close)
        lines.append("")
        if has_ai_commit:
            lines.append(
                "- 销售与AI是否一致 - 预测状态="
                + ("一致" if (forecast_val or "") == ai_commit and forecast_val else "不一致")
            )
        if has_ai_stage:
            lines.append(
                "- 销售与AI是否一致 - 商机阶段="
                + ("一致" if (stage_val or "") == ai_stage and stage_val else "不一致")
            )
        if has_ai_close:
            lines.append(
                "- 销售与AI是否一致 - 预计成交日期="
                + ("一致" if (close_date_val or "") == ai_close and close_date_val else "不一致")
            )
        lines.append("")

    # Minimal structured facts for chunk-level HAS_FACT extraction.
    lines.append("## KG/Vector Facts")
    lines.append("")
    lines.append(
        f"- [事实] 范围类型=商机；范围名称={opp_display}；指标=销售判断预测状态 (sales_forecast)；当前值={forecast_val or '缺失'}。"
    )
    lines.append(
        f"- [事实] 范围类型=商机；范围名称={opp_display}；指标=销售判断商机阶段 (sales_stage)；当前值={stage_val or '缺失'}。"
    )
    lines.append(
        f"- [事实] 范围类型=商机；范围名称={opp_display}；指标=销售判断预计成交日期 (sales_close_date)；当前值={close_date_val or '缺失'}。"
    )
    if has_ai_commit:
        lines.append(
            "- [事实] 范围类型=商机；范围名称="
            f"{opp_display}；指标=销售与AI预测状态一致性 (sales_ai_forecast_match)；"
            f"当前值={'一致' if (forecast_val or '') == ai_commit and forecast_val else '不一致'}；"
            f"销售值={forecast_val or '缺失'}；AI值={ai_commit}。"
        )
    if has_ai_stage:
        lines.append(
            "- [事实] 范围类型=商机；范围名称="
            f"{opp_display}；指标=销售与AI商机阶段一致性 (sales_ai_stage_match)；"
            f"当前值={'一致' if (stage_val or '') == ai_stage and stage_val else '不一致'}；"
            f"销售值={stage_val or '缺失'}；AI值={ai_stage}。"
        )
    if has_ai_close:
        lines.append(
            "- [事实] 范围类型=商机；范围名称="
            f"{opp_display}；指标=销售与AI预计成交日期一致性 (sales_ai_close_date_match)；"
            f"当前值={'一致' if (close_date_val or '') == ai_close and close_date_val else '不一致'}；"
            f"销售值={close_date_val or '缺失'}；AI值={ai_close}。"
        )
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
        lines.append(f"- **归类**: {category}")
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
    lines.append("")
    severity = getattr(risk_progress, "severity", None)
    # KG-friendly normalized fact lines to improve stable extraction.
    scope_tail_for_fact = scope_tail or "全局"
    lines.append("## KG/Vector Facts")
    lines.append("")
    fact_parts = [
        f"记录性质={type_label}",
        f"类型={type_name or '缺失'}",
        f"范围类型={st_zh or st or '缺失'}",
        f"范围名称={scope_tail_for_fact}",
        f"快照周期={getattr(risk_progress, 'snapshot_period', '') or '缺失'}",
    ]
    # Scope-aware dimensions: only include relevant attributes to reduce noise.
    if st_key == "opportunity":
        fact_parts.append(f"关联商机={opp_name or '范围名称未解析'}")
        if owner_name:
            fact_parts.append(f"负责人={owner_name}")
        if department_name:
            fact_parts.append(f"部门={department_name}")
    elif st_key == "owner":
        fact_parts.append(f"负责人={scope_tail_for_fact}")
        if department_name:
            fact_parts.append(f"部门={department_name}")
    elif st_key == "department":
        fact_parts.append(f"部门={scope_tail_for_fact}")
    elif st_key == "company":
        fact_parts.append("公司范围=全公司")

    lines.append(f"- [事实] {'；'.join(fact_parts)}。")
    if severity:
        lines.append(f"- [事实] 严重程度={severity}")
    lines.append("")

    # --- BRD dimensions (category is under 上下文 as 大类) ---
    level = getattr(risk_progress, "level", None)
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

    # NOTE(review-rp): `evidence` is intentionally excluded from indexed text for
    # now. Re-enable after evidence schema/query usage is finalized.

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

    # NOTE(review-rp): Audit fields are intentionally excluded from indexed text
    # to keep retrieval focused on business signals.

    return lines
