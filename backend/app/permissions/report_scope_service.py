"""日报/周报生成阶段的团队统计口径（OAuth org_scope）。

真源：aptsell-oauth docs/data-scope-matrix.md §2.1（日报/周报 = autoflow 定时统计 + 卡片推送）
及 §3.2「日报/周报」行。设计约定：**生成任务按维度使用 org_scope 规则确定统计人群**，
不走 crm_data_authority。

维度 → data-scope 实体（团队维度才需展开人群）：
- 团队日报 daily_report_team    → SALES_MANAGER / VIRTUAL_TEAM_LEAD = ORG_TEAM_SUB
- 团队周报 weekly_report_team   → 同上
（个人维度天然 SELF_ONLY 一人一卡、公司维度 GLOBAL 全量聚合，均无需在此展开）

团队人群锚点：部门负责人（user_department_relation 中该部门 is_leader=True 者）。以负责人的
user_id/crm_user_id 调 `get_data_scope`，再经 `map_org_scope_from_filters` 展开为汇报链
users.id（含负责人本人）。任一环节缺失（无负责人 / 无 org_scope / 展开为空）→ 返回 None，
交由调用方回退旧的部门成员展开。
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple
from uuid import UUID

from sqlmodel import Session

from app.permissions.user_id_resolver import (
    map_crm_user_ids_to_user_ids,
    map_org_scope_from_filters,
)
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

DAILY_REPORT_TEAM_ENTITY = "daily_report_team"
WEEKLY_REPORT_TEAM_ENTITY = "weekly_report_team"

# 返回结构：(canonical_id -> display_name, alias_owner_id -> canonical_id)
# 与 CRMStatisticsService._resolve_department_owners_for_todo_stats 对齐，可直接替换。
TeamOwners = Tuple[Dict[str, str], Dict[str, str]]


class ReportScopeService:
    def resolve_team_owners(
        self,
        session: Session,
        department_name: str,
        *,
        entity: str = DAILY_REPORT_TEAM_ENTITY,
    ) -> Optional[TeamOwners]:
        """按 OAuth org_scope 解析某部门团队统计人群。

        Returns:
            (people, alias_to_canonical) 或 None（表示应回退旧逻辑）。
            canonical 取 users.id 字符串，与个人日报 recorder_id / crm_todos owner 对齐。
        """
        name = (department_name or "").strip()
        if not name:
            return None

        # 延迟导入避免 app.repositories 包初始化期的循环依赖。
        from app.repositories.department_mirror import department_mirror_repo
        from app.repositories.user_department_relation import (
            user_department_relation_repo,
        )
        from app.repositories.user_profile import user_profile_repo

        dept_ids = department_mirror_repo.get_department_ids_by_name(session, name)
        leader = user_department_relation_repo.get_department_leader(session, dept_ids)
        if leader is None:
            logger.info("report team scope: no leader for department=%s, fallback", name)
            return None

        leader_user_id = str(leader.user_id or "").strip()
        leader_crm_user_id = str(leader.crm_user_id or "").strip()
        # 负责人关系缺 user_id 时，用 user_profiles 按 crm_user_id 回补。
        if not leader_user_id and leader_crm_user_id:
            mapped = map_crm_user_ids_to_user_ids(session, [leader_crm_user_id])
            leader_user_id = mapped[0] if mapped else ""
            if leader_user_id:
                logger.info(
                    "report team scope: leader user_id backfilled from user_profiles "
                    "(crm_user_id=%s) department=%s",
                    leader_crm_user_id,
                    name,
                )
        if not leader_user_id:
            logger.info(
                "report team scope: leader has no resolvable user_id "
                "(crm_user_id=%s) department=%s, fallback",
                leader_crm_user_id or "-",
                name,
            )
            return None

        try:
            manager_user_id = UUID(leader_user_id)
        except (ValueError, TypeError):
            logger.info(
                "report team scope: invalid leader user_id=%s department=%s, fallback",
                leader_user_id,
                name,
            )
            return None

        scope = oauth_client.get_data_scope(
            user_id=manager_user_id,
            crm_user_id=leader_crm_user_id or None,
            entity=entity,
        )
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        team_user_ids = map_org_scope_from_filters(session, filters)
        if not team_user_ids:
            logger.info(
                "report team scope: empty org_scope for department=%s entity=%s leader=%s, fallback",
                name,
                entity,
                manager_user_id,
            )
            return None

        uuids: list[UUID] = []
        seen: set[UUID] = set()
        for uid in team_user_ids:
            try:
                parsed = uid if isinstance(uid, UUID) else UUID(str(uid))
            except (ValueError, TypeError):
                continue
            if parsed not in seen:
                seen.add(parsed)
                uuids.append(parsed)
        if not uuids:
            return None

        people: Dict[str, str] = {}
        alias_to_canonical: Dict[str, str] = {}
        for profile in user_profile_repo.get_by_user_ids(session, uuids):
            if not profile.user_id:
                continue
            canonical = str(profile.user_id)
            people[canonical] = (profile.name or "").strip() or canonical
            alias_to_canonical[canonical] = canonical
            crm_user_id = str(profile.crm_user_id or "").strip()
            if crm_user_id:
                alias_to_canonical[crm_user_id] = canonical

        if not people:
            logger.info(
                "report team scope: org_scope resolved %d users but no profiles for department=%s, fallback",
                len(uuids),
                name,
            )
            return None

        logger.info(
            "report team scope: department=%s entity=%s members=%d (OAuth org_scope)",
            name,
            entity,
            len(people),
        )
        return people, alias_to_canonical


report_scope_service = ReportScopeService()
