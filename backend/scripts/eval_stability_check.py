#!/usr/bin/env python3
"""
批量测试 CRM 质量评估稳定性脚本（可直接运行）

用法：
1) 在项目根目录运行
2) 确保后端依赖和环境变量已加载（尤其 ARK_*）
3) 执行：
   python scripts/eval_stability_check.py --runs 10

可选参数：
- --runs 10            每条样本重复次数
- --only followup      只测 followup（followup/next/all）
- --json report.json   导出明细 JSON
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

# 兼容从 backend/scripts 直接运行：将 backend 目录加入模块搜索路径
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.crm.save_engine import (
    assess_followup_quality_bilingual,
    assess_next_steps_quality_bilingual,
)


@dataclass
class Sample:
    sample_id: str
    kind: str  # followup | next
    text_zh: str = ""
    text_en: str = ""
    expected: str = ""  # 仅用于人工对照，不作为断言


SAMPLES: List[Sample] = [
    # ---------- followup ----------
    Sample("F1", "followup", text_zh='1. 跟进内容（如：沟通事项）2. 客户反馈（如：观点/异议）3. 结论（如有）', expected="不合格"),
    Sample("F2", "followup", text_zh="待补充", expected="不合格"),
    Sample("F3", "followup", text_zh="test 123 asdf", expected="不合格"),
    Sample("F4", "followup", text_zh="今天和客户沟通了，整体顺利，后续持续跟进。", expected="不合格"),
    Sample("F5", "followup", text_zh="1) 与客户IT负责人确认现网慢查询问题主要在报表模块。2) 演示了索引优化方案和灰度步骤。3) 客户反馈可先在测试库验证，担心业务高峰影响。", expected="合格"),
    Sample("F6", "followup", text_zh="向客户说明了读写分离改造范围，客户确认先覆盖订单查询链路，并要求周五前给回滚预案。", expected="合格"),
    Sample("F7", "followup", text_zh="详细介绍了方案优势，客户认可，后续继续推进。", expected="不合格"),
    Sample("F8", "followup", text_zh="上午与客户DBA和架构师复盘Q1性能瓶颈，确认高峰时段写入延迟集中在库存服务；现场演示参数调优与连接池隔离方案。客户明确反馈可接受两阶段切换，并提出需先验证审计合规。双方达成下周三前完成压测并在周会上评审上线窗口。", expected="优秀"),
    Sample("M1", "followup", text_en="Discussed migration scope with CTO; client requested rollback strategy and security checklist before pilot.", expected="qualified+"),
    # # ---------- next ----------
    Sample("N1", "next", text_zh="1. 待办事项（如：具体动作）2. 时间节点（如：完成时间）3. 预期成果", expected="不合格"),
    Sample("N2", "next", text_zh="TBD", expected="不合格"),
    Sample("N3", "next", text_zh="保持沟通，等待客户反馈。", expected="不合格"),
    Sample("N4", "next", text_zh="发送方案并安排评审会议。", expected="不合格"),
    Sample("N5", "next", text_zh="下周持续跟进客户。", expected="不合格"),
    Sample("N6", "next", text_zh="本周四前提交PoC清单，周五与客户技术团队开30分钟评审会，确认测试范围。", expected="合格"),
    Sample("N7", "next", text_zh="客户已明确今年无预算，商机关闭，后续仅保持季度触达。", expected="合格"),
    Sample("N8", "next", text_zh="明天发送报价，周三电话沟通采购条款。", expected="合格"),
    Sample("N9", "next", text_zh="今天下班前发送分阶段实施计划；周二与客户安全负责人评审权限模型；周四完成试点环境联调。目标是在本月底前推动客户内部立项并锁定一期范围。", expected="优秀"),
    Sample("M2", "next", text_en="Opportunity closed - customer declined due to budget freeze, no next steps.", expected="qualified"),
]


def eval_one(sample: Sample) -> Dict[str, Any]:
    t0 = time.perf_counter()
    if sample.kind == "followup":
        r = assess_followup_quality_bilingual(sample.text_zh, sample.text_en)
        level_zh = r.get("followup_quality_level_zh", "")
        level_en = r.get("followup_quality_level_en", "")
        reason_zh = r.get("followup_quality_reason_zh", "")
        reason_en = r.get("followup_quality_reason_en", "")
    else:
        r = assess_next_steps_quality_bilingual(sample.text_zh, sample.text_en)
        level_zh = r.get("next_steps_quality_level_zh", "")
        level_en = r.get("next_steps_quality_level_en", "")
        reason_zh = r.get("next_steps_quality_reason_zh", "")
        reason_en = r.get("next_steps_quality_reason_en", "")
    dt = int((time.perf_counter() - t0) * 1000)
    return {
        "sample_id": sample.sample_id,
        "kind": sample.kind,
        "expected": sample.expected,
        "level_zh": level_zh,
        "level_en": level_en,
        "reason_zh": reason_zh,
        "reason_en": reason_en,
        "latency_ms": dt,
    }


def majority_ratio(items: List[str]) -> float:
    if not items:
        return 0.0
    c = Counter(items)
    return c.most_common(1)[0][1] / len(items)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10, help="每条样本重复次数")
    parser.add_argument("--only", choices=["followup", "next", "all"], default="all")
    parser.add_argument("--json", dest="json_path", default="", help="导出明细 JSON 路径")
    args = parser.parse_args()

    samples = SAMPLES
    if args.only != "all":
        samples = [s for s in samples if s.kind == args.only]

    all_records: List[Dict[str, Any]] = []
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    print(f"Running {len(samples)} samples x {args.runs} runs ...\n")
    for s in samples:
        for _ in range(args.runs):
            rec = eval_one(s)
            all_records.append(rec)
            grouped[s.sample_id].append(rec)

    # per-sample summary
    print("=== Per-sample summary ===")
    stable_count = 0
    for s in samples:
        recs = grouped[s.sample_id]
        zh_levels = [x["level_zh"] for x in recs]
        c = Counter(zh_levels)
        top_label, top_cnt = c.most_common(1)[0]
        ratio = top_cnt / len(zh_levels)
        if ratio >= 0.9:
            stable_count += 1
        latencies = [x["latency_ms"] for x in recs]
        p50 = int(statistics.median(latencies))
        print(
            f"{s.sample_id:>3} [{s.kind:<8}] "
            f"expected={s.expected:<10} "
            f"majority={top_label:<6} "
            f"consistency={ratio:.0%} "
            f"dist={dict(c)} "
            f"p50={p50}ms"
        )

    # global summary
    print("\n=== Global summary ===")
    total_samples = len(samples)
    print(f"Samples: {total_samples}, Runs per sample: {args.runs}")
    print(f"Stable samples (>=90% same zh level): {stable_count}/{total_samples} ({stable_count/total_samples:.0%})")

    all_lat = [r["latency_ms"] for r in all_records]
    all_lat_sorted = sorted(all_lat)
    p50 = all_lat_sorted[int(0.50 * (len(all_lat_sorted) - 1))]
    p95 = all_lat_sorted[int(0.95 * (len(all_lat_sorted) - 1))]
    print(f"Latency p50={p50}ms, p95={p95}ms")

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "runs": args.runs,
                    "only": args.only,
                    "records": all_records,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"Saved details to: {args.json_path}")


if __name__ == "__main__":
    main()