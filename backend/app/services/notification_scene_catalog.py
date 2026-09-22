"""推送场景目录：资格来源、偏好策略、可用变体。

routing:
  instance — 跟单条记录/本人绑定，不能按职责范围自行开关
  eligible_set — 固定路由给出资格集（公司管理层 / 部门 leader）

preference:
  none — 无个人开关
  eligible_opt_out — 有资格的人默认接收，可关掉自己负责的那一类
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

ROUTING_INSTANCE = "instance"
ROUTING_ELIGIBLE_SET = "eligible_set"

PREF_NONE = "none"
PREF_ELIGIBLE_OPT_OUT = "eligible_opt_out"

SCENE_VISIT_RECORD = "visit_record"
SCENE_SALES_DAILY = "sales_daily"
SCENE_DEPARTMENT_DAILY = "department_daily"
SCENE_DEPARTMENT_HIGHLIGHTS = "department_highlights"
SCENE_DEPARTMENT_WEEKLY = "department_weekly"
SCENE_COMPANY_DAILY = "company_daily"
SCENE_COMPANY_HIGHLIGHTS = "company_highlights"
SCENE_COMPANY_WEEKLY = "company_weekly"
SCENE_REVIEW_SESSION = "review_session"

VARIANT_KPI_CARD = "kpi_card"
VARIANT_VISIT_CARD = "visit_card"
VARIANT_RECAP_LITE = "recap_lite"
VARIANT_VISIT_REPORT = "visit_report"
VARIANT_TODAY_HIGHLIGHTS = "today_highlights"
VARIANT_SUMMARY_MD = "summary_md"

REPORT_SLOTS = (
    SCENE_SALES_DAILY,
    SCENE_COMPANY_DAILY,
    SCENE_DEPARTMENT_DAILY,
    SCENE_COMPANY_WEEKLY,
    SCENE_DEPARTMENT_WEEKLY,
)

HIGHLIGHTS_SLOTS = (
    SCENE_COMPANY_HIGHLIGHTS,
    SCENE_DEPARTMENT_HIGHLIGHTS,
)

POLICY_SLOTS = REPORT_SLOTS + HIGHLIGHTS_SLOTS

TODAY_HIGHLIGHTS_SLOTS = (
    SCENE_SALES_DAILY,
    SCENE_COMPANY_HIGHLIGHTS,
    SCENE_DEPARTMENT_HIGHLIGHTS,
)

SUMMARY_MD_SLOTS = (SCENE_COMPANY_DAILY, SCENE_DEPARTMENT_DAILY)

REPORT_SLOT_ALIASES = {
    "sales_daily_report": SCENE_SALES_DAILY,
    "company_daily_report": SCENE_COMPANY_DAILY,
    "department_daily_report": SCENE_DEPARTMENT_DAILY,
    "company_weekly_report": SCENE_COMPANY_WEEKLY,
    "department_weekly_report": SCENE_DEPARTMENT_WEEKLY,
}


@dataclass(frozen=True)
class SceneSpec:
    scene: str
    title: str
    routing: str
    preference: str
    variants: Tuple[str, ...]
    requires_department: bool = False
    requires_record_id: bool = False
    includes_groups: bool = False
    description: str = ""


SCENES: Dict[str, SceneSpec] = {
    SCENE_VISIT_RECORD: SceneSpec(
        scene=SCENE_VISIT_RECORD,
        title="拜访卡片",
        routing=ROUTING_INSTANCE,
        preference=PREF_NONE,
        variants=(VARIANT_VISIT_CARD, VARIANT_RECAP_LITE),
        requires_record_id=True,
        description="录入人 / 协同人 / 汇报上级 / 抄送 / 部门群；旁观走 cc_rules",
    ),
    SCENE_SALES_DAILY: SceneSpec(
        scene=SCENE_SALES_DAILY,
        title="销售个人日报",
        routing=ROUTING_INSTANCE,
        preference=PREF_ELIGIBLE_OPT_OUT,
        variants=(VARIANT_KPI_CARD, VARIANT_TODAY_HIGHLIGHTS),
        description="仅本人；today_highlights 为今日重点（本人当天的另一种组织形式）",
    ),
    SCENE_DEPARTMENT_DAILY: SceneSpec(
        scene=SCENE_DEPARTMENT_DAILY,
        title="部门日报",
        routing=ROUTING_ELIGIBLE_SET,
        preference=PREF_ELIGIBLE_OPT_OUT,
        variants=(VARIANT_KPI_CARD, VARIANT_SUMMARY_MD),
        requires_department=True,
        includes_groups=True,
        description="部门负责人 + department_review 群；summary_md 为日拜访报告 Markdown，读 crm_department_daily_summary.summary_content",
    ),
    SCENE_DEPARTMENT_HIGHLIGHTS: SceneSpec(
        scene=SCENE_DEPARTMENT_HIGHLIGHTS,
        title="部门今日重点",
        routing=ROUTING_ELIGIBLE_SET,
        preference=PREF_ELIGIBLE_OPT_OUT,
        variants=(VARIANT_TODAY_HIGHLIGHTS,),
        requires_department=True,
        description="仅部门负责人，不进群；与部门日报资格/通道独立",
    ),
    SCENE_DEPARTMENT_WEEKLY: SceneSpec(
        scene=SCENE_DEPARTMENT_WEEKLY,
        title="部门周报",
        routing=ROUTING_ELIGIBLE_SET,
        preference=PREF_ELIGIBLE_OPT_OUT,
        variants=(VARIANT_KPI_CARD, VARIANT_VISIT_REPORT),
        requires_department=True,
        includes_groups=True,
        description="部门负责人 + department_review 群；visit_report 为周拜访报告 Markdown，读 crm_weekly_followup_summary.report_kind=visit_report",
    ),
    SCENE_COMPANY_DAILY: SceneSpec(
        scene=SCENE_COMPANY_DAILY,
        title="公司日报",
        routing=ROUTING_ELIGIBLE_SET,
        preference=PREF_ELIGIBLE_OPT_OUT,
        variants=(VARIANT_KPI_CARD, VARIANT_SUMMARY_MD),
        description="OAuth notification:daily_report_company:receive（kpi_card）；summary_md 为日拜访报告 Markdown，读 crm_department_daily_summary.summary_content，资格为 recipient_user_ids",
    ),
    SCENE_COMPANY_HIGHLIGHTS: SceneSpec(
        scene=SCENE_COMPANY_HIGHLIGHTS,
        title="公司今日重点",
        routing=ROUTING_ELIGIBLE_SET,
        preference=PREF_ELIGIBLE_OPT_OUT,
        variants=(VARIANT_TODAY_HIGHLIGHTS,),
        description="report_push_policy.recipient_user_ids 指定接收人；与公司日报名单独立，无 OAuth receive",
    ),
    SCENE_COMPANY_WEEKLY: SceneSpec(
        scene=SCENE_COMPANY_WEEKLY,
        title="公司周报",
        routing=ROUTING_ELIGIBLE_SET,
        preference=PREF_ELIGIBLE_OPT_OUT,
        variants=(VARIANT_KPI_CARD, VARIANT_VISIT_REPORT),
        description="OAuth notification:weekly_report_company:receive；visit_report 为周拜访报告 Markdown，读 crm_weekly_followup_summary.report_kind=visit_report"
    ),
    SCENE_REVIEW_SESSION: SceneSpec(
        scene=SCENE_REVIEW_SESSION,
        title="Review 阶段通知",
        routing=ROUTING_INSTANCE,
        preference=PREF_NONE,
        variants=(VARIANT_KPI_CARD,),
        description="CRMReviewAttendee 出席人",
    ),
}


def normalize_scene(scene: Optional[str]) -> str:
    raw = str(scene or "").strip()
    return REPORT_SLOT_ALIASES.get(raw, raw)


def get_scene(scene: Optional[str]) -> Optional[SceneSpec]:
    return SCENES.get(normalize_scene(scene))


def list_scenes() -> List[SceneSpec]:
    return list(SCENES.values())


def scene_allows_variant(scene: str, variant: str) -> bool:
    spec = get_scene(scene)
    if not spec:
        return False
    return variant in spec.variants
