import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from app.core.config import settings
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_opportunities import CRMOpportunity
from app.models.crm_accounts import CRMAccount
from app.models.crm_user import CRMUser
from app.models.user_department_relation import UserDepartmentRelation
from app.models.crm_weekly_followup_entity_summary import CRMWeeklyFollowupEntitySummary
from app.models.crm_weekly_followup_summary import CRMWeeklyFollowupSummary
from app.repositories.department_mirror import department_mirror_repo
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
        if limit <= 0 or len(s) <= limit:
            return s
        return s[: max(0, limit - 3)] + "..."

    def _visit_context_for_prompt(
        self, record: CRMSalesVisitRecord, cache: dict[int, str]
    ) -> str:
        """拜访摘要文本：按 record.id 缓存，避免实体 LLM 与多部门 rollup 重复拼装。"""
        limit = settings.CRM_WEEKLY_FOLLOWUP_VISIT_CONTEXT_MAX_CHARS
        rid = record.id
        if rid is not None and rid in cache:
            return cache[rid]
        raw = crm_writeback_service.generate_visit_summary_content(record) or ""
        text = self._truncate(raw.strip(), limit)
        if rid is not None:
            cache[rid] = text
        return text

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

    def _build_entity_prompt(
        self,
        key: _EntityKey,
        records: List[Dict[str, Any]],
        *,
        preamble: str = "",
    ) -> str:
        """
        records: 已经被拼装处理后的结构化列表（按时间排序）
        preamble: 可选说明（例如仅节选部分拜访时的口径提示）
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
        preamble_block = f"{preamble}\n" if preamble else ""
        return f"""
你是销售管理周复盘助手。请基于“本周拜访记录摘要（较完整）”，输出该【{key.department_name}】团队下该实体的“本周进展与风险”结构化总结。

{preamble_block}实体信息（同一组拜访记录对应同一实体）：
- 客户: {account_name or '--'}
- 商机: {opportunity_name or '--'}
- 伙伴: {partner_name or '--'}

任务目标：
- progress：总结该实体本周最有价值的推进结果（结论优先），并给出关键依据与下一步动作（可选）。
- risks：只总结会影响推进的明确风险/分歧/阻塞项；无明确风险必须输出空字符串。

输出要求：
1) 输出必须是严格 JSON（不要 markdown、不要代码块、不要多余文字）。
2) 字段必须包含：
   - progress: string（用于“列表页单行/两行展示”，务必精炼）
   - risks: string（用于列表页展示；可为空字符串）
3) 输出格式示例（仅作结构示例，请基于实际内容生成）：
{{
  "progress": "本周完成需求澄清并确认POC范围，客户认可方案方向，下周三前提交实施计划。",
  "risks": "客户预算审批周期不确定，可能影响立项时间。"
}}
4) 列表页展示优化（非常重要）：
   - progress：1-3 句中文，优先“结论 -> 依据 -> 下一步（可选）”；避免逐条流水账；建议 <= 120 字
   - risks：如无明确风险输出 ""；如有风险，最多 3 条关键信息（可用分号分隔），总字数建议 <= 120
5) 归纳规则：
   - 综合多次拜访记录提炼“关键变化/达成共识/客户承诺/下一步”，不要照搬原文
   - 仅基于给定摘要生成，不得编造时间、金额、人员、承诺、结论
   - 若记录间存在冲突或说法反复，必须在 risks 中明确指出冲突点
   - 若同一信息重复出现，合并去重后再输出
   - 避免空泛套话（如“总体进展良好”），应尽量具体、可执行、可验证
6) 质量门槛（必须满足）：
   - progress 必须体现“本周发生了什么实质推进”（如确认范围、明确决策路径、锁定下一步动作）
   - 没有可验证推进时，progress 应如实写“推进有限 + 当前状态”，不得强行写积极结论
   - risks 仅写负面因素或不确定性；不要把普通待办写成风险

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
        visit_context_cache: Optional[dict[int, str]] = None,
    ) -> str:
        """
        生成“团队/公司”层面的周汇总 prompt（输出纯文本中文，不要 JSON）。
        拜访量大时按实体节选最近若干条并复用 visit_context_cache，控制 prompt 体积。
        """
        cache = visit_context_cache if visit_context_cache is not None else {}
        rollup_cap = settings.CRM_WEEKLY_FOLLOWUP_ROLLUP_MAX_VISITS_PER_ENTITY
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

            new_part = "（本周首次拜访）" if _is_new_entity(record_pairs_sorted) else ""
            lines.append(f"- {t}{owner_part}{new_part}")
            visits_slice = record_pairs_sorted
            if rollup_cap > 0 and len(record_pairs_sorted) > rollup_cap:
                visits_slice = record_pairs_sorted[-rollup_cap:]
                lines.append(
                    f"  - （该实体本周共 {len(record_pairs_sorted)} 条拜访，以下仅列时间最近的 {rollup_cap} 条摘要）"
                )
            for r in visits_slice:
                day = (r.visit_communication_date.isoformat() if r.visit_communication_date else "--")
                ctx = (self._visit_context_for_prompt(r, cache) or "").strip() or "--"
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
你是销售团队管理者的周复盘助手。请基于"本周拜访记录摘要（按实体分组）"，输出管理者视角的周跟进总结（自然语言），用于管理层快速阅读与决策。

硬性要求（必须同时满足）：
1) 输出必须是【纯中文文本】，不要 JSON、不要 markdown、不要编号/项目符号。
2) 输出字数请严格控制在【最多 150 字】（包含标点与换行）；输出格式为每个要点一段话自然语言，共 4 段，简洁清晰、可读性高；如超过 150 字，必须自行进一步压缩。
3) 只基于输入内容归纳，不要编造；客户/商机名称用输入中的名称；不要逐条复述原文，侧重归纳与抽象。
4) 【重要】输出格式要求：必须输出连续的 4 段文本，段落之间用单个换行符分隔，绝对不要有空段落、不要有多余空行、不要有连续的两个换行符。正确的格式示例：第一段内容\n第二段内容\n第三段内容\n第四段内容（其中\n表示换行，实际输出时就是换行，不要输出\n字符本身）。
   
   第一段 - 机会推进说明：
   - 首先列出本周新增客户（本周有首次拜访的实体，要列出所有新增客户名称）。
   - 然后按阶段分类说明客户进展（阶段固定为：初步接洽、需求澄清、方案讨论、商务决策阶段），说明每个阶段下有哪些客户。
   第二段 - 进展说明：
   - 判断客户是否形成了正向和对推进有实质意义的结果。
   - 对进展进行分类抽象，仅保留涉及客户数量最多的前三至四类，并按客户涉及数量由高到低排序。
   - 每一类可以列举 1-2 个客户名称。   
   第三段 - 卡点及风险：
   - 归纳在多个项目出现的共性问题，说明会带来什么风险。
   - 卡点是指客户存在异议的、需要解决的问题。
   - 对卡点进行归类，按涉及客户数量由高到低列出前三或前四类，每一类说具体的客户名称。
   第四段 - 下一步管理者需要重点关注：
   - 基于现存的卡点和风险以及下一步工作计划，写 2-3 条需要管理者特别关注或者需要参与的事情（要具体、可执行）。

上下文：
- {header}
- 本周涉及实体数：{total_entities}
- 本周拜访记录数（去重前）：{total_visits}
- 本周新增实体（首次拜访）：{new_entities_text}

本周拜访记录摘要（按实体分组）：
{input_text}

【再次强调输出格式】：
- 输出必须是 4 段连续的文本，每段之间只有一个换行符，不要有任何空行
- 输出格式应该是：第一段文字\n第二段文字\n第三段文字\n第四段文字（连续输出，中间无空行）
- 绝对禁止在段落之间插入空行或空段落
""".strip()

    def _parse_llm_json(self, raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        try:
            parsed = json.loads(raw.strip())
            return parsed if isinstance(parsed, dict) else None
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

    def _list_active_departments_for_empty_summary(
        self, session: Session
    ) -> List[Tuple[str, str]]:
        """仅包含有 leader（user_department_relation.is_leader）的有效部门，用于生成「本周没有跟进记录」的部门总结。"""
        leader_dept_ids = user_department_relation_repo.list_department_ids_with_leader(session)
        return [
            (did, name)
            for did, name in department_mirror_repo.list_active_departments(session)
            if did in leader_dept_ids
        ]

    def _upsert_empty_department_summary(
        self, session: Session, week_start: date, week_end: date, dept_id: str, dept_name: str
    ) -> None:
        """为指定部门写入 summary_content=\"本周没有跟进记录\" 的部门总结（无 entity 明细）。"""
        summary_obj = CRMWeeklyFollowupSummary(
            week_start=week_start,
            week_end=week_end,
            summary_type="department",
            department_id=dept_id,
            department_name=dept_name,
            title=self._build_summary_title(
                week_start=week_start,
                week_end=week_end,
                summary_type="department",
                department_name=dept_name,
            ),
            summary_content="本周没有跟进记录",
        )
        self._upsert_summary(session, summary_obj)

    def _upsert_empty_company_summary(
        self, session: Session, week_start: date, week_end: date
    ) -> None:
        """写入 summary_content=\"本周没有跟进记录\" 的公司级总结。"""
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
            summary_content="本周没有跟进记录",
        )
        self._upsert_summary(session, company_obj)

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

        # 读取本周拜访记录（证据来源）；仅加载周跟进所需列，降低大字段 IO
        visit_select_cols = (
            CRMSalesVisitRecord.id,
            CRMSalesVisitRecord.account_name,
            CRMSalesVisitRecord.account_id,
            CRMSalesVisitRecord.opportunity_name,
            CRMSalesVisitRecord.opportunity_id,
            CRMSalesVisitRecord.partner_name,
            CRMSalesVisitRecord.partner_id,
            CRMSalesVisitRecord.contacts,
            CRMSalesVisitRecord.contact_position,
            CRMSalesVisitRecord.contact_name,
            CRMSalesVisitRecord.collaborative_participants,
            CRMSalesVisitRecord.visit_communication_date,
            CRMSalesVisitRecord.visit_communication_method,
            CRMSalesVisitRecord.visit_purpose,
            CRMSalesVisitRecord.last_modified_time,
            CRMSalesVisitRecord.followup_record,
            CRMSalesVisitRecord.next_steps,
            CRMSalesVisitRecord.remarks,
            CRMSalesVisitRecord.is_first_visit,
            CRMSalesVisitRecord.recorder_id,
        )
        stmt = (
            select(CRMSalesVisitRecord)
            .options(load_only(*visit_select_cols))
            .where(
                CRMSalesVisitRecord.visit_communication_date.isnot(None),
                CRMSalesVisitRecord.visit_communication_date >= week_start,
                CRMSalesVisitRecord.visit_communication_date <= week_end,
                CRMSalesVisitRecord.recorder_id.isnot(None),
            )
        )
        records = session.exec(stmt).all()
        if not records:
            logger.warning("该周没有任何拜访记录，仍为有 leader 的部门生成空总结")
            active_departments = self._list_active_departments_for_empty_summary(session)
            for dept_id, dept_name in active_departments:
                self._upsert_empty_department_summary(
                    session, week_start, week_end, dept_id, dept_name
                )
            self._upsert_empty_company_summary(session, week_start, week_end)
            logger.info(f"周跟进总结生成完成（无拜访记录）：部门 {len(active_departments)} 个")
            return {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "entity_count": 0,
                "departments": len(active_departments),
            }

        # 按拜访主键缓存摘要文本，供实体 LLM 与多部门/公司 rollup 复用
        visit_context_cache: dict[int, str] = {}
        llm_max_concurrency = max(1, settings.CRM_WEEKLY_FOLLOWUP_LLM_MAX_CONCURRENCY)

        # 批量准备：CRM 商机/客户，用于获取负责人（不是拜访记录填写人）
        opportunity_ids = {str(r.opportunity_id).strip() for r in records if r.opportunity_id}
        account_ids = {str(r.account_id).strip() for r in records if r.account_id}
        # 仅有 partner_id（无商机、无客户）时，也需要从客户表中查负责人
        partner_ids = {str(r.partner_id).strip() for r in records if r.partner_id}

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

        # 合作伙伴：同样从客户表中查负责人（按 unique_id == partner_id 匹配）
        partner_by_id: dict[str, dict[str, Optional[str]]] = {}
        if partner_ids:
            partner_rows = session.exec(
                select(
                    CRMAccount.unique_id,
                    CRMAccount.customer_name,
                    CRMAccount.person_in_charge_id,
                ).where(CRMAccount.unique_id.in_(list(partner_ids)))
            ).all()
            for unique_id, customer_name, person_in_charge_id in partner_rows:
                if not unique_id:
                    continue
                pid = str(unique_id).strip()
                partner_by_id[pid] = {
                    "unique_id": pid,
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
        for pid, partner in partner_by_id.items():
            owner_id = self._norm_id(partner.get("person_in_charge_id"))
            owner_crm_user_id_by_entity[("partner", pid)] = owner_id
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
            elif entity_type == "partner":
                owner_crm_user_id = owner_crm_user_id_by_entity.get(("partner", entity_id))

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

        # 预热缓存，减少并行阶段重复拼接上下文
        for r in records:
            self._visit_context_for_prompt(r, visit_context_cache)

        entity_materialized: dict[
            _EntityKey, tuple[List[CRMSalesVisitRecord], CRMSalesVisitRecord]
        ] = {}
        entity_prompts: dict[_EntityKey, str] = {}
        for key, record_pairs in grouped.items():
            record_pairs_sorted = sorted(
                record_pairs,  # type: ignore[arg-type]
                key=lambda x: (
                    x.visit_communication_date or date.min,
                    x.last_modified_time or datetime.min,
                    x.id or 0,
                ),
            )
            last_record = record_pairs_sorted[-1]
            entity_materialized[key] = (record_pairs_sorted, last_record)

            entity_cap = settings.CRM_WEEKLY_FOLLOWUP_ENTITY_LLM_MAX_VISITS
            record_pairs_for_llm = record_pairs_sorted
            preamble = ""
            if entity_cap > 0 and len(record_pairs_sorted) > entity_cap:
                record_pairs_for_llm = record_pairs_sorted[-entity_cap:]
                preamble = (
                    f"【输入说明】该实体本周共 {len(record_pairs_sorted)} 条拜访记录；"
                    f"以下为按时间最近的 {entity_cap} 条摘要，用于归纳本周进展与风险。"
                    "证据字段仍关联本周全部拜访记录 ID；请勿编造未出现在摘要中的具体事实。\n"
                )

            compressed: List[Dict[str, Any]] = []
            for r in record_pairs_for_llm:
                compressed.append(
                    {
                        "id": r.id,
                        "opportunity_name": r.opportunity_name,
                        "account_name": r.account_name,
                        "partner_name": r.partner_name,
                        "context": self._visit_context_for_prompt(r, visit_context_cache),
                    }
                )
            entity_prompts[key] = self._build_entity_prompt(
                key, list(reversed(compressed)), preamble=preamble
            )  # 由早到晚

        llm_entity_result_by_key: dict[_EntityKey, tuple[str, str]] = {}

        def _run_entity_llm(item: tuple[_EntityKey, str]) -> tuple[_EntityKey, str, str]:
            key, prompt = item
            progress = ""
            risks = ""
            try:
                raw = call_ark_llm(
                    prompt,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
                parsed = self._parse_llm_json(raw)
                if parsed:
                    progress = str(parsed.get("progress") or "").strip()
                    risks = str(parsed.get("risks") or "").strip()
            except Exception as e:
                logger.warning(f"LLM 生成失败，key={key}: {e}")
            return key, progress, risks

        with ThreadPoolExecutor(max_workers=llm_max_concurrency) as executor:
            futures = [executor.submit(_run_entity_llm, item) for item in entity_prompts.items()]
            for fut in as_completed(futures):
                key, progress, risks = fut.result()
                llm_entity_result_by_key[key] = (progress, risks)

        persisted_entities: List[CRMWeeklyFollowupEntitySummary] = []
        owner_name_by_key: dict[_EntityKey, Optional[str]] = {}

        for key, (record_pairs_sorted, last_record) in entity_materialized.items():
            progress, risks = llm_entity_result_by_key.get(key, ("", ""))

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
            elif key.entity_type == "partner":
                partner = partner_by_id.get(key.entity_id)
                if partner:
                    # partner_id/partner_name 保持与字段语义一致；名称优先用客户表中的 customer_name
                    partner_id = str((partner.get("unique_id") or "") or partner_id or "")
                    partner_name = partner.get("customer_name") or partner_name

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
                evidence_record_ids=json.dumps(
                    [r.id for r in record_pairs_sorted if r.id is not None],
                    ensure_ascii=False,
                ),
            )
            persisted = self._upsert_entity_summary(session, entity_obj)
            persisted_entities.append(persisted)

        # 生成部门/公司汇总（仅 LLM）
        # 按部门组织架构：为负责人直接部门及所有上级部门生成汇总
        owner_dept_ids = {
            dept_by_user_id.get(owner_user_id_by_key[k])
            for k in grouped
            if dept_by_user_id.get(owner_user_id_by_key.get(k))
        }
        ancestor_chains = department_mirror_repo.get_ancestor_chains_bulk(
            session, owner_dept_ids
        )

        by_dept_id_full: Dict[str, List[Tuple[_EntityKey, List[CRMSalesVisitRecord]]]] = defaultdict(list)
        by_dept_name_fallback: Dict[str, List[Tuple[_EntityKey, List[CRMSalesVisitRecord]]]] = defaultdict(list)
        for key in grouped:
            owner_user_id = owner_user_id_by_key.get(key)
            dept_id = dept_by_user_id.get(owner_user_id) if owner_user_id else None
            dept_id_norm = str(dept_id).strip() if dept_id else None
            # 有部门 ID 且能解析出祖先链时，当前部门及每一层上级部门都会收到该 key 的汇总
            if dept_id_norm and dept_id_norm in ancestor_chains:
                for did, _ in ancestor_chains[dept_id_norm]:
                    by_dept_id_full[did].append((key, grouped[key]))
            else:
                by_dept_name_fallback[key.department_name].append((key, grouped[key]))

        dept_name_by_id = department_mirror_repo.get_department_names_by_ids(
            session, by_dept_id_full.keys()
        )
        names_covered_by_id = set(dept_name_by_id.values())

        unknown_department_record_groups: List[Tuple[_EntityKey, List[CRMSalesVisitRecord]]] = []
        seen_keys_unknown: set[_EntityKey] = set()
        rollup_requests: List[tuple[str, str, List[Tuple[_EntityKey, List[CRMSalesVisitRecord]]]]] = []
        for dept_id, record_groups in by_dept_id_full.items():
            department_name = dept_name_by_id.get(dept_id, "未知部门")
            if department_name == "未知部门":
                for k, rs in record_groups:
                    if k not in seen_keys_unknown:
                        seen_keys_unknown.add(k)
                        unknown_department_record_groups.append((k, rs))
                continue
            rollup_requests.append(("department", department_name, record_groups))

        if unknown_department_record_groups:
            rollup_requests.append(("department", "未知部门", unknown_department_record_groups))

        for dept_name, record_groups in by_dept_name_fallback.items():
            if not dept_name or dept_name in names_covered_by_id:
                continue
            rollup_requests.append(("department", dept_name, record_groups))

        all_record_groups = list(grouped.items())
        rollup_requests.append(("company", "公司", all_record_groups))

        # 关键：rollup prompt 在主线程构建，避免 commit 后 ORM 对象在子线程触发懒加载
        # 导致同一 DB 连接跨线程访问（例如 PyMySQL packet sequence 错误）。
        rollup_prompt_by_scope_dept: dict[tuple[str, str], str] = {}
        for scope, dept, record_groups in rollup_requests:
            rollup_prompt_by_scope_dept[(scope, dept)] = self._build_rollup_prompt(
                week_start=week_start,
                week_end=week_end,
                scope=scope,
                department_name=dept,
                record_groups=record_groups,
                owner_name_by_key=owner_name_by_key,
                visit_context_cache=visit_context_cache,
            )

        rollup_text_by_scope_dept: dict[tuple[str, str], Optional[str]] = {}

        def _run_rollup_llm(item: tuple[tuple[str, str], str]) -> tuple[str, str, Optional[str]]:
            (scope, dept), prompt = item
            try:
                raw = call_ark_llm(prompt, temperature=0.4)
                txt = (raw or "").strip()
                txt = txt.strip().strip("`").strip()
                txt = re.sub(r'\n\s*\n+', '\n', txt)
                return scope, dept, (txt if txt else None)
            except Exception as e:
                logger.warning(f"LLM 汇总生成失败 scope={scope} dept={dept}: {e}")
                return scope, dept, ""

        with ThreadPoolExecutor(max_workers=llm_max_concurrency) as executor:
            futures = [
                executor.submit(_run_rollup_llm, item)
                for item in rollup_prompt_by_scope_dept.items()
            ]
            for fut in as_completed(futures):
                scope, dept, txt = fut.result()
                rollup_text_by_scope_dept[(scope, dept)] = txt

        # upsert department summaries（按部门 ID 的层级 + 无 dept_id 的 fallback）
        # 表唯一约束为 (week_start, week_end, summary_type, department_name)，多个未在 mirror 的
        # dept_id 都会得到 department_name="未知部门"，需合并为一条汇总再 upsert，避免冲突
        for dept_id, record_groups in by_dept_id_full.items():
            department_name = dept_name_by_id.get(dept_id, "未知部门")
            if department_name != "未知部门":
                summary_obj = CRMWeeklyFollowupSummary(
                    week_start=week_start,
                    week_end=week_end,
                    summary_type="department",
                    department_id=dept_id,
                    department_name=department_name,
                    title=self._build_summary_title(
                        week_start=week_start,
                        week_end=week_end,
                        summary_type="department",
                        department_name=department_name,
                    ),
                    summary_content=rollup_text_by_scope_dept.get(("department", department_name)),
                )
                self._upsert_summary(session, summary_obj)

        if unknown_department_record_groups:
            summary_obj = CRMWeeklyFollowupSummary(
                week_start=week_start,
                week_end=week_end,
                summary_type="department",
                department_id="",
                department_name="未知部门",
                title=self._build_summary_title(
                    week_start=week_start,
                    week_end=week_end,
                    summary_type="department",
                    department_name="未知部门",
                ),
                summary_content=rollup_text_by_scope_dept.get(("department", "未知部门")),
            )
            self._upsert_summary(session, summary_obj)

        for dept_name, record_groups in by_dept_name_fallback.items():
            if not dept_name or dept_name in names_covered_by_id:
                continue
            summary_obj = CRMWeeklyFollowupSummary(
                week_start=week_start,
                week_end=week_end,
                summary_type="department",
                department_id="",
                department_name=dept_name,
                title=self._build_summary_title(
                    week_start=week_start,
                    week_end=week_end,
                    summary_type="department",
                    department_name=dept_name,
                ),
                summary_content=rollup_text_by_scope_dept.get(("department", dept_name)),
            )
            self._upsert_summary(session, summary_obj)

        # company summary
        company_text = rollup_text_by_scope_dept.get(("company", "公司"))
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

        # 补全无拜访记录的部门：仍生成 summary_content="本周没有跟进记录"（仅含在 relation 中有 leader 的部门）
        # 注意：by_dept_id_full 已按祖先链展开（见上文 ancestor_chains），子部门有记录时其父部门也在
        # by_dept_id_full 中并已写入 LLM 汇总，此处不会对父部门误写空总结
        active_departments = self._list_active_departments_for_empty_summary(session)
        empty_dept_count = 0
        for dept_id, dept_name in active_departments:
            if dept_id not in by_dept_id_full:
                self._upsert_empty_department_summary(
                    session, week_start, week_end, dept_id, dept_name
                )
                empty_dept_count += 1

        known_dept_count = sum(
            1 for did in by_dept_id_full if dept_name_by_id.get(did, "未知部门") != "未知部门"
        )
        dept_count = (
            known_dept_count
            + (1 if unknown_department_record_groups else 0)
            + sum(
                1 for n in by_dept_name_fallback if n and n not in names_covered_by_id
            )
            + empty_dept_count
        )
        logger.info(
            f"周跟进总结生成完成：实体 {len(persisted_entities)} 条，部门 {dept_count} 个（含上级，其中无记录 {empty_dept_count} 个）"
        )
        return {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "entity_count": len(persisted_entities),
            "departments": dept_count,
        }


crm_weekly_followup_service = CRMWeeklyFollowupService()


