from typing import Dict, List

METRIC_DISPLAY_NAMES: Dict[str, str] = {
    "opp_count": "商机数",
    "target": "目标",
    "closed": "已成单",
    "gap": "差额",
    "pipeline_coverage": "倍数",
    "commit_sales": "销售确定下单",
    "commit_ai": "AI确定下单",
    "upside_sales": "销售可能下单",
}

METRIC_DESCRIPTIONS: Dict[str, str] = {
    "opp_count": "本周期商机总数",
    "target": "部门/个人销售目标金额",
    "closed": "已关单赢单金额",
    "gap": "目标与已成单之差（target − closed）",
    "pipeline_coverage": "Pipeline 覆盖倍数 = (commit_sales + upside_sales) / gap（gap ≤ 0 时为 0）",
    "commit_sales": "销售自己判定会成单的金额",
    "commit_ai": "AI 判定会成单的金额",
    "upside_sales": "销售判定可能成单的金额",
}

AMOUNT_METRIC_NAMES = {
    "target",
    "closed",
    "gap",
    "commit_sales",
    "commit_ai",
    "upside_sales",
}


def build_metric_glossary_markdown() -> str:
    ordered_metrics: List[str] = [
        "opp_count",
        "target",
        "closed",
        "gap",
        "pipeline_coverage",
        "commit_sales",
        "commit_ai",
        "upside_sales",
    ]
    lines = [
        "| 中文名 | metric_name | 说明 |",
        "|--------|-------------|------|",
    ]
    for metric_name in ordered_metrics:
        lines.append(
            f"| {METRIC_DISPLAY_NAMES[metric_name]} | {metric_name} | {METRIC_DESCRIPTIONS[metric_name]} |"
        )
    lines.extend(
        [
            "",
            "每个指标的数据字段:",
            "- metric_value: 本次 review session 的值",
            "- metric_value_prev: 上次的值",
            "- metric_delta: 本次与上次的变化量",
            "- metric_rate: 本次与上次的变化率（小数，如 0.15 = 15%）",
        ]
    )
    return "\n".join(lines)
