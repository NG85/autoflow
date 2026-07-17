from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select, or_

from app.models.notification_cc_rule import (
    SCOPE_TYPE_DEPARTMENT,
    SCOPE_TYPE_GLOBAL,
    SCOPE_TYPE_USER,
    NotificationCcRule,
)
from app.repositories.base_repo import BaseRepo
from app.repositories.department_mirror import department_mirror_repo

# cc_scope 优先级：user > department > global（同一人同时命中时取更具体维度）
_CC_SCOPE_RANK = {
    SCOPE_TYPE_USER: 3,
    SCOPE_TYPE_DEPARTMENT: 2,
    SCOPE_TYPE_GLOBAL: 1,
}


class NotificationCcRuleRepo(BaseRepo):
    model_cls = NotificationCcRule

    def list_enabled_rules_for_recorder(
        self,
        db_session: Session,
        *,
        event_type: str,
        recorder_user_id: UUID,
        recorder_department_id: Optional[str] = None,
    ) -> List[NotificationCcRule]:
        """
        返回命中 recorder 的 enabled 规则（user / department / global）。

        department 匹配：
        - 精确：scope_department_id == recorder_department_id
        - include_children：配置部门在录入人部门的祖先链上（含自身）
        """
        dept_id = (recorder_department_id or "").strip()
        ancestor_ids: List[str] = []
        if dept_id:
            chains = department_mirror_repo.get_ancestor_chains_bulk(db_session, [dept_id])
            ancestor_ids = [aid for aid, _ in chains.get(dept_id, []) if aid]

        match_conditions = [
            (NotificationCcRule.scope_type == SCOPE_TYPE_USER)
            & (NotificationCcRule.scope_user_id == recorder_user_id),
            NotificationCcRule.scope_type == SCOPE_TYPE_GLOBAL,
        ]
        if dept_id:
            match_conditions.append(
                (NotificationCcRule.scope_type == SCOPE_TYPE_DEPARTMENT)
                & (NotificationCcRule.scope_department_id == dept_id)
            )
            if ancestor_ids:
                match_conditions.append(
                    (NotificationCcRule.scope_type == SCOPE_TYPE_DEPARTMENT)
                    & (NotificationCcRule.include_children == True)  # noqa: E712
                    & (NotificationCcRule.scope_department_id.in_(ancestor_ids))
                )

        stmt = (
            select(NotificationCcRule)
            .options(selectinload(NotificationCcRule.recipients))
            .where(
                NotificationCcRule.event_type == event_type,
                NotificationCcRule.enabled == True,  # noqa: E712
                or_(*match_conditions),
            )
            .order_by(NotificationCcRule.priority.desc(), NotificationCcRule.id.asc())
        )
        return list(db_session.exec(stmt).all())

    def merge_recipient_scopes(self, rules: List[NotificationCcRule]) -> List[Tuple[UUID, str]]:
        """
        合并多条规则的抄送人，返回 (user_id, cc_scope)。
        cc_scope 为 user / department / global；同一人同时命中时取更具体维度（user > department > global）。
        """
        scope_by_user: Dict[UUID, str] = {}
        ordered: List[UUID] = []
        for rule in rules:
            if rule.scope_type == SCOPE_TYPE_USER:
                rule_scope = SCOPE_TYPE_USER
            elif rule.scope_type == SCOPE_TYPE_DEPARTMENT:
                rule_scope = SCOPE_TYPE_DEPARTMENT
            else:
                rule_scope = SCOPE_TYPE_GLOBAL
            for recipient in rule.recipients or []:
                uid = recipient.user_id
                if uid is None:
                    continue
                if uid not in ordered:
                    ordered.append(uid)
                existing = scope_by_user.get(uid)
                if existing is None or _CC_SCOPE_RANK.get(rule_scope, 0) > _CC_SCOPE_RANK.get(
                    existing, 0
                ):
                    scope_by_user[uid] = rule_scope
        return [(uid, scope_by_user.get(uid, SCOPE_TYPE_GLOBAL)) for uid in ordered]


notification_cc_rule_repo = NotificationCcRuleRepo()
