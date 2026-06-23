from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select, or_

from app.models.notification_cc_rule import (
    EVENT_TYPE_VISIT_RECORD_CARD,
    SCOPE_TYPE_GLOBAL,
    SCOPE_TYPE_USER,
    NotificationCcRule,
)
from app.repositories.base_repo import BaseRepo


class NotificationCcRuleRepo(BaseRepo):
    model_cls = NotificationCcRule

    def list_enabled_rules_for_recorder(
        self,
        db_session: Session,
        *,
        event_type: str,
        recorder_user_id: UUID,
    ) -> List[NotificationCcRule]:
        """返回命中 recorder 的 enabled 规则（user 维度 + global 维度）。"""
        stmt = (
            select(NotificationCcRule)
            .options(selectinload(NotificationCcRule.recipients))
            .where(
                NotificationCcRule.event_type == event_type,
                NotificationCcRule.enabled == True,  # noqa: E712
                or_(
                    (NotificationCcRule.scope_type == SCOPE_TYPE_USER)
                    & (NotificationCcRule.scope_user_id == recorder_user_id),
                    NotificationCcRule.scope_type == SCOPE_TYPE_GLOBAL,
                ),
            )
            .order_by(NotificationCcRule.priority.desc(), NotificationCcRule.id.asc())
        )
        return list(db_session.exec(stmt).all())

    def merge_recipient_user_ids(self, rules: List[NotificationCcRule]) -> List[UUID]:
        """合并多条规则的抄送人 user_id，保持首次出现顺序。"""
        return [user_id for user_id, _ in self.merge_recipient_scopes(rules)]

    def merge_recipient_scopes(self, rules: List[NotificationCcRule]) -> List[Tuple[UUID, str]]:
        """
        合并多条规则的抄送人，返回 (user_id, cc_scope)。
        cc_scope 为 user（按销售配置）或 global（全局规则）；同一人同时命中时 user 优先。
        """
        scope_by_user: Dict[UUID, str] = {}
        ordered: List[UUID] = []
        for rule in rules:
            rule_scope = "user" if rule.scope_type == SCOPE_TYPE_USER else "global"
            for recipient in rule.recipients or []:
                uid = recipient.user_id
                if uid is None:
                    continue
                if uid not in ordered:
                    ordered.append(uid)
                if rule_scope == "user":
                    scope_by_user[uid] = "user"
                elif uid not in scope_by_user:
                    scope_by_user[uid] = "global"
        return [(uid, scope_by_user.get(uid, "global")) for uid in ordered]


notification_cc_rule_repo = NotificationCcRuleRepo()
