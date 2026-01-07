import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_opportunities import CRMOpportunity
from app.models.crm_accounts import CRMAccount
from app.models.crm_user import CRMUser
from app.models.user_profile import UserProfile
from app.models.user_department_relation import UserDepartmentRelation
from app.models.crm_weekly_followup_entity_summary import CRMWeeklyFollowupEntitySummary
from app.models.crm_weekly_followup_summary import CRMWeeklyFollowupSummary
from app.repositories.user_department_relation import user_department_relation_repo
from app.services.crm_writeback_service import crm_writeback_service
from app.utils.ark_llm import call_ark_llm

logger = logging.getLogger(__name__)


def get_sunday_to_saturday_week_range(today: date) -> Tuple[date, date]:
    """
    与现有周报一致：默认处理“上周日 - 本周六”
    """
    # 0=周一,...,6=周日
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday + 7)
    this_saturday = last_sunday + timedelta(days=6)
    return last_sunday, this_saturday


@dataclass(frozen=True)
class _EntityKey:
    department_name: str
    entity_type: str  # opportunity/account/partner
    entity_id: str


class CRMWeeklyFollowupService:
    """
    生成并持久化“周跟进总结”：
    - 明细：按（团队 + 商机/客户/合作伙伴）输出本周进展、风险/问题
    - 汇总：按团队/公司输出整体描述
    """

    def _pick_entity(self, record: CRMSalesVisitRecord) -> Optional[Tuple[str, str]]:
        if record.opportunity_id:
            return "opportunity", str(record.opportunity_id).strip()
        if record.account_id:
            return "account", str(record.account_id).strip()
        if record.partner_id:
            return "partner", str(record.partner_id).strip()
        return None

    def _truncate(self, s: str, limit: int) -> str:
        s = (s or "").strip()
        if len(s) <= limit:
            return s
        return s[: max(0, limit - 3)] + "..."

    def _norm_id(self, v: Optional[str]) -> Optional[str]:
        s = (str(v).strip() if v is not None else "")
        return s or None

    def _build_summary_title(self, *, week_start: date, week_end: date, summary_type: str, department_name: str) -> str:
        # 格式约定：
        # - 公司：周跟进总结-[2025-12-21至2025-12-27]
        # - 团队：团队[XXX]周跟进总结-[2025-12-21至2025-12-27]
        week_part = f"{week_start.isoformat()}至{week_end.isoformat()}"
        if summary_type == "company":
            return f"周跟进总结-[{week_part}]"
        dept = department_name or "未知团队"
        return f"团队[{dept}]周跟进总结-[{week_part}]"

    def _build_entity_prompt(self, key: _EntityKey, records: List[Dict[str, Any]]) -> str:
        """
        records: 已经被拼装处理后的结构化列表（按时间排序）
        """
        # 同一组 records 对应同一个实体（商机/客户/伙伴），提取名称用于上下文理解
        def _pick_first_non_empty(field: str) -> str:
            for r in records:
                v = r.get(field)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    return s
            return ""

        opportunity_name = _pick_first_non_empty("opportunity_name")
        account_name = _pick_first_non_empty("account_name")
        partner_name = _pick_first_non_empty("partner_name")

        visits_text = "\n".join(
            [
                (
                    f"- 拜访详情: {r.get('context') or '--'}"
                )
                for r in records
            ]
        )
        return f"""
你是销售管理的周复盘助手。请基于“本周拜访记录摘要（较完整）”，输出该【{key.department_name}】团队下该实体的一周跟进总结。

实体信息（同一组拜访记录对应同一实体）：
- 客户: {account_name or '--'}
- 商机: {opportunity_name or '--'}
- 伙伴: {partner_name or '--'}

要求：
1) 输出必须是严格 JSON（不要 markdown、不要多余文字）。
2) 字段必须包含：
   - progress: string（用于“列表页单行/两行展示”，务必精炼）
   - risks: string（用于列表页展示；可为空字符串）
3) 列表页展示优化（非常重要）：
   - progress：1-3 句中文，优先给“结论 + 关键依据 + 下一步动作（可选）”，避免长段落与逐条复述；建议 <= 120 字
   - risks：如无明确风险输出空字符串；如有风险，最多给出 3 条，每条建议 <= 40 字（总字数建议 <= 120）
4) 归纳规则：
   - 综合多次拜访记录提炼关键变化/共识/承诺/下一步；不要照搬原文
   - 若多条记录互相矛盾，请在 risks 中加以说明
   - 避免空泛套话（如“总体进展良好”），尽量具体可执行

本周拜访记录摘要：
{visits_text}
""".strip()

    def _build_rollup_prompt(
        self,
        *,
        week_start: date,
        week_end: date,
        scope: str,
        department_name: str,
        record_groups: List[Tuple[_EntityKey, List[CRMSalesVisitRecord]]],
        owner_name_by_key: Optional[dict[_EntityKey, Optional[str]]] = None,
    ) -> str:
        """
        生成“团队/公司”层面的周汇总 prompt（输出纯文本中文，不要 JSON）。
        """
        # 不做输入压缩：基于“原始拜访记录分组”（按实体聚合），保留全部实体与全部拜访摘要
        def _entity_title_from_record(last_record: CRMSalesVisitRecord) -> str:
            customer = (last_record.account_name or last_record.partner_name or "--").strip()
            opp = (last_record.opportunity_name or "").strip()
            return f"{customer}" + (f" / {opp}" if opp else "")

        def _group_last_key(records: List[CRMSalesVisitRecord]) -> Tuple[date, datetime]:
            """
            汇总分组排序：优先按“拜访日期”，同日再按“最新更新时间”兜底。
            """
            last_visit = date.min
            last_modified = datetime.min
            for r in records:
                if r.visit_communication_date and r.visit_communication_date > last_visit:
                    last_visit = r.visit_communication_date
                if r.last_modified_time and r.last_modified_time > last_modified:
                    last_modified = r.last_modified_time
            return last_visit, last_modified

        picked = sorted(record_groups, key=lambda x: _group_last_key(x[1]))

        # 本周新增实体：该实体分组中任意一条拜访记录 is_first_visit == True
        def _is_new_entity(records: List[CRMSalesVisitRecord]) -> bool:
            for r in records:
                if bool(getattr(r, "is_first_visit", False)):
                    return True
            return False

        new_entity_titles: List[str] = []
        for key, records in picked:
            if not records:
                continue
            if _is_new_entity(records):
                # 用该实体“最后一条记录”的实体名称做展示（与后续分组标题一致）
                record_pairs_sorted = sorted(
                    records,
                    key=lambda x: (
                        x.visit_communication_date or date.min,
                        x.last_modified_time or datetime.min,
                        x.id or 0,
                    ),
                )
                last_record = record_pairs_sorted[-1]
                new_entity_titles.append(_entity_title_from_record(last_record))

        lines: List[str] = []
        for key, records in picked:
            if not records:
                continue
            record_pairs_sorted = sorted(
                records,
                key=lambda x: (
                    x.visit_communication_date or date.min,
                    x.last_modified_time or datetime.min,
                    x.id or 0,
                ),
            )
            last_record = record_pairs_sorted[-1]
            t = _entity_title_from_record(last_record)

            owner = ""
            if owner_name_by_key:
                owner = (owner_name_by_key.get(key) or "").strip()
            owner_part = f"（负责人:{owner}）" if owner else ""

            # 全量保留该实体本周的拜访摘要（不截断）
            new_part = "（本周首次拜访）" if _is_new_entity(record_pairs_sorted) else ""
            lines.append(f"- {t}{owner_part}{new_part}")
            for r in record_pairs_sorted:
                day = (r.visit_communication_date.isoformat() if r.visit_communication_date else "--")
                ctx = (crm_writeback_service.generate_visit_summary_content(r) or "").strip() or "--"
                lines.append(f"  - [{day}] {ctx}")

        week_part = f"{week_start.isoformat()}至{week_end.isoformat()}"
        if scope == "company":
            header = f"公司周跟进总结，周期[{week_part}]。"
        else:
            header = f"团队[{department_name}]周跟进总结，周期[{week_part}]。"

        total_entities = len(record_groups)
        total_visits = sum(len(rs) for _, rs in record_groups)
        input_text = "\n".join(lines) if lines else "- 无可用明细"
        new_entities_text = "、".join(dict.fromkeys([x for x in new_entity_titles if (x or "").strip()])) or "无"

        return f"""
你是销售团队管理者的周复盘助手。请基于“本周拜访记录摘要（按实体分组）”，输出【2-3 个自然段】管理者视角的周跟进总结（自然语言），用于管理层快速阅读与决策。

硬性要求（必须同时满足）：
1) 输出必须是【纯中文文本】，不要 JSON、不要 markdown、不要编号/项目符号。
2) 输出字数请严格控制在【150 字以内】（包含标点与换行）；允许用换行做自然分段（2-3 段），但不要用列表格式；如超过 150 字，必须自行进一步压缩到 150 字以内。
3) 只基于输入内容归纳，不要编造；客户/商机名称用输入中的名称；不要逐条复述原文，侧重归纳与抽象。
4) 内容必须按以下要点依次覆盖，但要自然地写在同一段里（可用“；”“。”等分隔）：
   - 机会推进说明：先点出本周新增客户（本周有首次拜访的实体），只需举例点到客户即可（不必罗列全部）；再按阶段归类客户进展（阶段固定为：初步接洽、需求澄清、方案讨论、商务决策阶段），每个阶段同样只需举例 1-3 个代表客户。
   - 进展（共识）：提炼每个客户跟进形成的共识/结论，并将共识归类为 3-4 类；每一类只需举例点到 1-3 个代表客户（不要罗列全部客户名）。
   - 共性卡点及风险：归纳多个项目反复出现的卡点/异议/待解决问题，并说明会带来的风险影响；卡点归类 3-4 类，每一类只需举例点到 1-3 个代表客户（不要罗列全部客户名）。
   - 下一步 leader 需要重点关注：基于现存卡点/风险与下一步计划，给出 2-3 条管理者需要参与/推动/重点盯防的事项（要具体、可执行）。

上下文：
- {header}
- 本周涉及实体数：{total_entities}
- 本周拜访记录数（去重前）：{total_visits}
- 本周新增实体（首次拜访）：{new_entities_text}

本周拜访记录摘要（按实体分组）：
{input_text}
""".strip()

    def _parse_llm_json(self, raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        raw = raw.strip()
        # 兼容模型偶发输出前后包裹文本：尽量提取第一个 JSON 对象
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None

    def _upsert_entity_summary(self, session: Session, obj: CRMWeeklyFollowupEntitySummary) -> CRMWeeklyFollowupEntitySummary:
        """
        幂等键：week_start/week_end/department_name/entity_type/entity_id
        注意：不覆盖人工评论字段 comments
        """
        existing = session.exec(
            select(CRMWeeklyFollowupEntitySummary).where(
                CRMWeeklyFollowupEntitySummary.week_start == obj.week_start,
                CRMWeeklyFollowupEntitySummary.week_end == obj.week_end,
                CRMWeeklyFollowupEntitySummary.department_name == obj.department_name,
                CRMWeeklyFollowupEntitySummary.entity_type == obj.entity_type,
                CRMWeeklyFollowupEntitySummary.entity_id == obj.entity_id,
            )
        ).first()
        if existing:
            existing.account_id = obj.account_id
            existing.account_name = obj.account_name
            existing.opportunity_id = obj.opportunity_id
            existing.opportunity_name = obj.opportunity_name
            existing.partner_id = obj.partner_id
            existing.partner_name = obj.partner_name
            existing.owner_user_id = obj.owner_user_id
            existing.owner_name = obj.owner_name
            existing.progress = obj.progress
            existing.risks = obj.risks
            existing.evidence_record_ids = obj.evidence_record_ids
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        try:
            session.add(obj)
            session.commit()
            session.refresh(obj)
            return obj
        except IntegrityError:
            # 并发兜底：再查一次
            session.rollback()
            existing = session.exec(
                select(CRMWeeklyFollowupEntitySummary).where(
                    CRMWeeklyFollowupEntitySummary.week_start == obj.week_start,
                    CRMWeeklyFollowupEntitySummary.week_end == obj.week_end,
                    CRMWeeklyFollowupEntitySummary.department_name == obj.department_name,
                    CRMWeeklyFollowupEntitySummary.entity_type == obj.entity_type,
                    CRMWeeklyFollowupEntitySummary.entity_id == obj.entity_id,
                )
            ).first()
            if not existing:
                raise
            return existing

    def _upsert_summary(self, session: Session, obj: CRMWeeklyFollowupSummary) -> CRMWeeklyFollowupSummary:
        """
        幂等键：week_start/week_end/summary_type/department_name（company 行 department_name=""）
        """
        existing = session.exec(
            select(CRMWeeklyFollowupSummary).where(
                CRMWeeklyFollowupSummary.week_start == obj.week_start,
                CRMWeeklyFollowupSummary.week_end == obj.week_end,
                CRMWeeklyFollowupSummary.summary_type == obj.summary_type,
                CRMWeeklyFollowupSummary.department_name == obj.department_name,
            )
        ).first()
        if existing:
            existing.summary_content = obj.summary_content
            existing.title = obj.title
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        try:
            session.add(obj)
            session.commit()
            session.refresh(obj)
            return obj
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(CRMWeeklyFollowupSummary).where(
                    CRMWeeklyFollowupSummary.week_start == obj.week_start,
                    CRMWeeklyFollowupSummary.week_end == obj.week_end,
                    CRMWeeklyFollowupSummary.summary_type == obj.summary_type,
                    CRMWeeklyFollowupSummary.department_name == obj.department_name,
                )
            ).first()
            if not existing:
                raise
            return existing

    def generate_weekly_followup(
        self,
        session: Session,
        week_start: date,
        week_end: date,
    ) -> Dict[str, Any]:
        """
        生成并写入：
        - crm_weekly_followup_entity_summary
        - crm_weekly_followup_summary（department/company）
        """
        logger.info(f"开始生成周跟进总结，日期范围：{week_start} ~ {week_end}（周日到周六）")

        # 读取本周拜访记录（证据来源）
        stmt = (
            select(CRMSalesVisitRecord)
            .where(
                CRMSalesVisitRecord.visit_communication_date.isnot(None),
                CRMSalesVisitRecord.visit_communication_date >= week_start,
                CRMSalesVisitRecord.visit_communication_date <= week_end,
                CRMSalesVisitRecord.recorder_id.isnot(None),
            )
        )
        records = session.exec(stmt).all()
        if not records:
            logger.warning("该周没有任何拜访记录，跳过生成")
            return {"week_start": week_start.isoformat(), "week_end": week_end.isoformat(), "entity_count": 0, "departments": 0}

        # 批量准备：CRM 商机/客户，用于获取负责人（不是拜访记录填写人）
        opportunity_ids = {str(r.opportunity_id).strip() for r in records if r.opportunity_id}
        account_ids = {str(r.account_id).strip() for r in records if r.account_id}

        # 只缓存周跟进生成所需的少数字段，避免 select(CRMOpportunity) 拉全量大字段
        opp_by_id: dict[str, dict[str, Optional[str]]] = {}
        if opportunity_ids:
            opp_rows = session.exec(
                select(
                    CRMOpportunity.unique_id,
                    CRMOpportunity.opportunity_name,
                    CRMOpportunity.customer_id,
                    CRMOpportunity.customer_name,
                    CRMOpportunity.owner_id,
                ).where(CRMOpportunity.unique_id.in_(list(opportunity_ids)))
            ).all()
            for unique_id, opportunity_name, customer_id, customer_name, owner_id in opp_rows:
                if not unique_id:
                    continue
                oid = str(unique_id).strip()
                opp_by_id[oid] = {
                    "unique_id": oid,
                    "opportunity_name": (str(opportunity_name).strip() if opportunity_name else None),
                    "customer_id": (str(customer_id).strip() if customer_id else None),
                    "customer_name": (str(customer_name).strip() if customer_name else None),
                    "owner_id": (str(owner_id).strip() if owner_id else None),
                }

        # 同理：客户只取必要字段
        acc_by_id: dict[str, dict[str, Optional[str]]] = {}
        if account_ids:
            acc_rows = session.exec(
                select(
                    CRMAccount.unique_id,
                    CRMAccount.customer_name,
                    CRMAccount.person_in_charge_id,
                ).where(CRMAccount.unique_id.in_(list(account_ids)))
            ).all()
            for unique_id, customer_name, person_in_charge_id in acc_rows:
                if not unique_id:
                    continue
                aid = str(unique_id).strip()
                acc_by_id[aid] = {
                    "unique_id": aid,
                    "customer_name": (str(customer_name).strip() if customer_name else None),
                    "person_in_charge_id": (str(person_in_charge_id).strip() if person_in_charge_id else None),
                }

        # 批量准备：负责人（CRM 用户ID）与其部门（来自 crm_user）
        owner_crm_user_id_by_entity: dict[tuple[str, str], Optional[str]] = {}
        owner_crm_user_ids: set[str] = set()
        for oid, opp in opp_by_id.items():
            owner_id = self._norm_id(opp.get("owner_id"))
            owner_crm_user_id_by_entity[("opportunity", oid)] = owner_id
            if owner_id:
                owner_crm_user_ids.add(owner_id)
        for aid, acc in acc_by_id.items():
            owner_id = self._norm_id(acc.get("person_in_charge_id"))
            owner_crm_user_id_by_entity[("account", aid)] = owner_id
            if owner_id:
                owner_crm_user_ids.add(owner_id)

        crm_user_dept_by_owner_id: dict[str, str] = {}
        crm_user_name_by_owner_id: dict[str, str] = {}
        if owner_crm_user_ids:
            crm_users = session.exec(
                select(CRMUser.unique_id, CRMUser.department, CRMUser.user_name).where(
                    CRMUser.unique_id.in_(list(owner_crm_user_ids))
                )
            ).all()
            for uid, dept, uname in crm_users:
                if not uid:
                    continue
                key_uid = str(uid).strip()
                crm_user_dept_by_owner_id[key_uid] = (dept or "").strip()
                if uname:
                    crm_user_name_by_owner_id[key_uid] = str(uname).strip()

        # 先按实体聚合拜访记录（key 的 department_name 采用“负责人团队”口径）
        grouped: Dict[_EntityKey, List[CRMSalesVisitRecord]] = defaultdict(list)
        # 同时记录每个 key 对应的 owner 信息（后续写入 owner_user_id / owner_name / department_id）
        owner_crm_user_id_by_key: dict[_EntityKey, Optional[str]] = {}
        dept_name_by_key: dict[_EntityKey, str] = {}

        for record in records:
            picked = self._pick_entity(record)
            if not picked:
                continue
            entity_type, entity_id = picked
            entity_id = str(entity_id).strip()

            owner_crm_user_id = None
            dept_name = ""

            if entity_type == "opportunity":
                owner_crm_user_id = owner_crm_user_id_by_entity.get(("opportunity", entity_id))
            elif entity_type == "account":
                owner_crm_user_id = owner_crm_user_id_by_entity.get(("account", entity_id))

            # 团队口径：仅用负责人在 crm_user 里的 department（不做 recorder 兜底，避免口径混淆）
            if owner_crm_user_id:
                dept_name = crm_user_dept_by_owner_id.get(owner_crm_user_id, "")
            dept_name = dept_name.strip() or "未知部门"

            key = _EntityKey(department_name=dept_name, entity_type=entity_type, entity_id=entity_id)
            grouped[key].append(record)
            owner_crm_user_id_by_key[key] = owner_crm_user_id
            dept_name_by_key[key] = dept_name

        # 解析负责人到系统 user_id（使用 crm_user_id -> user_id）
        crm_user_ids = {v for v in owner_crm_user_id_by_key.values() if v}

        user_id_by_crm_user_id: dict[str, str] = {}
        if crm_user_ids:
            rows = session.exec(
                select(UserDepartmentRelation.crm_user_id, UserDepartmentRelation.user_id, UserDepartmentRelation.is_primary)
                .where(UserDepartmentRelation.crm_user_id.in_(list(crm_user_ids)))
                .order_by(UserDepartmentRelation.crm_user_id, UserDepartmentRelation.is_primary.desc())
            ).all()
            for crm_uid, uid, _ in rows:
                if not crm_uid or not uid:
                    continue
                k = str(crm_uid)
                if k not in user_id_by_crm_user_id:
                    user_id_by_crm_user_id[k] = str(uid)

        owner_user_id_by_key: dict[_EntityKey, Optional[str]] = {}
        for key in grouped.keys():
            crm_uid = owner_crm_user_id_by_key.get(key)
            uid = None
            if crm_uid:
                uid = user_id_by_crm_user_id.get(crm_uid)
            owner_user_id_by_key[key] = uid

        owner_name_by_user_id: dict[str, str] = {}
        if owner_user_ids := [x for x in owner_user_id_by_key.values() if x]:
            rows = session.exec(
                select(UserDepartmentRelation.user_id, UserDepartmentRelation.user_name, UserDepartmentRelation.is_primary)
                .where(UserDepartmentRelation.user_id.in_(list(owner_user_ids)))
                .order_by(UserDepartmentRelation.user_id, UserDepartmentRelation.is_primary.desc())
            ).all()
            for uid, uname, _ in rows:
                if not uid:
                    continue
                if str(uid) not in owner_name_by_user_id and uname:
                    owner_name_by_user_id[str(uid)] = str(uname)

        # 批量解析部门ID：按负责人 user_id 映射到主部门（优先权威 relation）
        owner_user_ids = [uid for uid in owner_user_id_by_key.values() if uid]
        dept_by_user_id = user_department_relation_repo.get_primary_department_by_user_ids(session, owner_user_ids)

        persisted_entities: List[CRMWeeklyFollowupEntitySummary] = []
        owner_name_by_key: dict[_EntityKey, Optional[str]] = {}

        for key, record_pairs in grouped.items():
            # 按日期 + 最后更新时间排序，取最后一条作为该实体的“最新状态参考”
            record_pairs_sorted = sorted(
                record_pairs,  # type: ignore[arg-type]
                key=lambda x: (
                    x.visit_communication_date or date.min,
                    x.last_modified_time or datetime.min,
                    x.id or 0,
                ),
            )
            last_record = record_pairs_sorted[-1]

            # 压缩输入记录
            compressed: List[Dict[str, Any]] = []
            for r in record_pairs_sorted:
                # 复用回写内容拼装逻辑，提供更多上下文，提升 LLM 评估精度
                context = crm_writeback_service.generate_visit_summary_content(r)
                compressed.append(
                    {
                        "id": r.id,
                        "opportunity_name": r.opportunity_name,
                        "account_name": r.account_name,
                        "partner_name": r.partner_name,
                        "context": context,
                    }
                )

            # LLM 生成
            progress = ""
            risks = ""
            prompt = self._build_entity_prompt(key, list(reversed(compressed)))  # 由早到晚
            try:
                raw = call_ark_llm(prompt, temperature=0.2)
                parsed = self._parse_llm_json(raw)
                if parsed:
                    progress = str(parsed.get("progress") or "").strip()
                    risks = str(parsed.get("risks") or "").strip()
            except Exception as e:
                logger.warning(f"LLM 生成失败，key={key}: {e}")

            # 组装实体行
            owner_user_id = owner_user_id_by_key.get(key)
            owner_name = owner_name_by_user_id.get(owner_user_id) if owner_user_id else None
            if not owner_name:
                crm_uid = owner_crm_user_id_by_key.get(key)
                if crm_uid:
                    owner_name = crm_user_name_by_owner_id.get(crm_uid)
            owner_name = (owner_name or "").strip() or None
            owner_name_by_key[key] = owner_name

            # 优先用 CRM 表里的名称（更权威/更新），兜底再用拜访记录字段
            account_id = last_record.account_id
            account_name = last_record.account_name
            opportunity_id = last_record.opportunity_id
            opportunity_name = last_record.opportunity_name
            partner_id = last_record.partner_id
            partner_name = last_record.partner_name

            if key.entity_type == "opportunity":
                opp = opp_by_id.get(key.entity_id)
                if opp:
                    opportunity_id = str((opp.get("unique_id") or "") or opportunity_id or "")
                    opportunity_name = opp.get("opportunity_name") or opportunity_name
                    account_id = opp.get("customer_id") or account_id
                    account_name = opp.get("customer_name") or account_name
            elif key.entity_type == "account":
                acc = acc_by_id.get(key.entity_id)
                if acc:
                    account_id = str((acc.get("unique_id") or "") or account_id or "")
                    account_name = acc.get("customer_name") or account_name

            entity_obj = CRMWeeklyFollowupEntitySummary(
                week_start=week_start,
                week_end=week_end,
                department_id=dept_by_user_id.get(owner_user_id) if owner_user_id else None,
                department_name=dept_name_by_key.get(key, key.department_name),
                entity_type=key.entity_type,
                entity_id=key.entity_id,
                account_id=account_id,
                account_name=account_name,
                opportunity_id=opportunity_id,
                opportunity_name=opportunity_name,
                partner_id=partner_id,
                partner_name=partner_name,
                owner_user_id=owner_user_id,
                owner_name=owner_name,
                progress=progress,
                risks=risks,
                evidence_record_ids=json.dumps([c["id"] for c in compressed if c.get("id") is not None], ensure_ascii=False),
            )
            persisted = self._upsert_entity_summary(session, entity_obj)
            persisted_entities.append(persisted)

        # 生成部门/公司汇总（仅 LLM）
        by_dept: Dict[str, List[Tuple[_EntityKey, List[CRMSalesVisitRecord]]]] = defaultdict(list)
        for key, rs in grouped.items():
            by_dept[key.department_name].append((key, rs))

        # 用实体行补齐 department_id（汇总内容不再依赖实体行）
        dept_id_by_name: Dict[str, str] = {}
        for e in persisted_entities:
            dn = (e.department_name or "").strip()
            did = str(getattr(e, "department_id", "") or "").strip()
            if dn and did and dn not in dept_id_by_name:
                dept_id_by_name[dn] = did

        def _llm_rollup_text(
            scope: str,
            dept: str,
            record_groups: List[Tuple[_EntityKey, List[CRMSalesVisitRecord]]],
        ) -> Optional[str]:
            try:
                prompt = self._build_rollup_prompt(
                    week_start=week_start,
                    week_end=week_end,
                    scope=scope,
                    department_name=dept,
                    record_groups=record_groups,
                    owner_name_by_key=owner_name_by_key,
                )
                raw = call_ark_llm(prompt, temperature=0.4)
                txt = (raw or "").strip()
                # 轻度清洗：去掉可能的 ``` 包裹
                txt = txt.strip().strip("`").strip()
                return txt if txt else None
            except Exception as e:
                logger.warning(f"LLM 汇总生成失败 scope={scope} dept={dept}: {e}")
                return ""

        # upsert department summaries
        for dept, record_groups in by_dept.items():
            dept_id = dept_id_by_name.get(dept, "")
            summary_obj = CRMWeeklyFollowupSummary(
                week_start=week_start,
                week_end=week_end,
                summary_type="department",
                department_id=dept_id,
                department_name=dept,
                title=self._build_summary_title(
                    week_start=week_start,
                    week_end=week_end,
                    summary_type="department",
                    department_name=dept,
                ),
                summary_content=_llm_rollup_text("department", dept, record_groups),
            )
            self._upsert_summary(session, summary_obj)

        # company summary
        all_record_groups = list(grouped.items())
        company_text = _llm_rollup_text("company", "公司", all_record_groups)
        company_obj = CRMWeeklyFollowupSummary(
            week_start=week_start,
            week_end=week_end,
            summary_type="company",
            department_id="",
            department_name="",
            title=self._build_summary_title(
                week_start=week_start,
                week_end=week_end,
                summary_type="company",
                department_name="",
            ),
            summary_content=company_text,
        )
        self._upsert_summary(session, company_obj)

        logger.info(
            f"周跟进总结生成完成：实体 {len(persisted_entities)} 条，部门 {len(by_dept)} 个"
        )
        return {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "entity_count": len(persisted_entities),
            "departments": len(by_dept),
        }


crm_weekly_followup_service = CRMWeeklyFollowupService()


