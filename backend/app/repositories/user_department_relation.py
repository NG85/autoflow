from __future__ import annotations

from typing import Iterable, Set

from sqlmodel import Session, select

from app.models.user_department_relation import UserDepartmentRelation
from app.repositories.base_repo import BaseRepo


class UserDepartmentRelationRepo(BaseRepo):
    model_cls = UserDepartmentRelation

    def get_is_leader_by_user_ids(
        self,
        db_session: Session,
        user_ids: Iterable[str],
    ) -> dict[str, bool]:
        """
        批量获取用户是否为 leader（按 user_id 分组）。

        规则：
        - 任意一条记录 is_leader=1，则认为该用户是 leader
        """
        ids = [x for x in (user_ids or []) if x]
        if not ids:
            return {}

        rows = db_session.exec(
            select(UserDepartmentRelation.user_id, UserDepartmentRelation.is_leader)
            .where(UserDepartmentRelation.user_id.in_(ids))
        ).all()

        result: dict[str, bool] = {}
        for user_id, is_leader in rows:
            if not user_id:
                continue
            # 只要出现 True 就锁定为 True
            result[user_id] = bool(result.get(user_id, False) or bool(is_leader))
        return result

    def list_department_ids_with_leader(self, db_session: Session) -> Set[str]:
        """
        返回至少有一条有效关系且 is_leader=True 的部门 department_id 集合。
        与 DepartmentMirror.unique_id 对齐，用于仅向「有负责人」的部门生成空周跟进总结等。
        """
        rows = db_session.exec(
            select(UserDepartmentRelation.department_id).where(
                UserDepartmentRelation.is_leader == True,  # noqa: E712
                UserDepartmentRelation.is_active == True,  # noqa: E712
            )
        ).all()
        return {str(did).strip() for did in rows if did and str(did).strip()}

    def get_primary_department_by_user_ids(
        self,
        db_session: Session,
        user_ids: Iterable[str],
    ) -> dict[str, str]:
        """
        批量获取用户主部门（按 user_id 分组）。

        规则：
        - 优先 is_primary=1 的记录
        - 若没有主部门记录，则取第一条（按 id 升序）
        """
        ids = [x for x in (user_ids or []) if x]
        if not ids:
            return {}

        rows = db_session.exec(
            select(UserDepartmentRelation)
            .where(UserDepartmentRelation.user_id.in_(ids))
            .order_by(UserDepartmentRelation.user_id, UserDepartmentRelation.is_primary.desc(), UserDepartmentRelation.id)
        ).all()

        result: dict[str, str] = {}
        for r in rows:
            if not r.user_id or not r.department_id:
                continue
            if r.user_id not in result:
                result[r.user_id] = r.department_id
        return result

    def get_primary_department_by_crm_user_ids(
        self,
        db_session: Session,
        crm_user_ids: Iterable[str],
    ) -> dict[str, str]:
        """
        批量获取用户主部门（按 crm_user_id 分组）。

        规则：
        - 优先 is_primary=1 的记录
        - 若没有主部门记录，则取第一条（按 id 升序）
        """
        ids = [x for x in (crm_user_ids or []) if x]
        if not ids:
            return {}

        rows = db_session.exec(
            select(UserDepartmentRelation)
            .where(UserDepartmentRelation.crm_user_id.in_(ids))
            .order_by(
                UserDepartmentRelation.crm_user_id,
                UserDepartmentRelation.is_primary.desc(),
                UserDepartmentRelation.id,
            )
        ).all()

        result: dict[str, str] = {}
        for r in rows:
            if not r.crm_user_id or not r.department_id:
                continue
            if r.crm_user_id not in result:
                result[r.crm_user_id] = r.department_id
        return result

    def get_user_names_by_crm_user_ids(
        self,
        db_session: Session,
        crm_user_ids: Iterable[str],
    ) -> dict[str, str]:
        """
        批量获取用户姓名（按 crm_user_id 分组）。

        规则：
        - 优先 is_primary=1 的记录
        - 若没有主记录，则取第一条（按 id 升序）
        - 只考虑 is_active=1 的记录
        """
        ids = [x for x in (crm_user_ids or []) if x]
        if not ids:
            return {}

        rows = db_session.exec(
            select(UserDepartmentRelation)
            .where(
                UserDepartmentRelation.crm_user_id.in_(ids),
                UserDepartmentRelation.is_active == True,  # noqa: E712
            )
            .order_by(
                UserDepartmentRelation.crm_user_id,
                UserDepartmentRelation.is_primary.desc(),
                UserDepartmentRelation.id,
            )
        ).all()

        result: dict[str, str] = {}
        for r in rows:
            if not r.crm_user_id or not r.user_name:
                continue
            if r.crm_user_id not in result:
                result[r.crm_user_id] = r.user_name
        return result

    def get_primary_department_by_user_names(
        self,
        db_session: Session,
        user_names: Iterable[str],
    ) -> dict[str, str]:
        """
        批量获取用户主部门（按 user_name 分组）。

        规则：
        - 优先 is_primary=1 的记录
        - 若没有主部门记录，则取第一条（按 id 升序）
        - 只考虑 is_active=1 的记录
        """
        names = [x for x in (user_names or []) if x]
        if not names:
            return {}

        rows = db_session.exec(
            select(UserDepartmentRelation)
            .where(
                UserDepartmentRelation.user_name.in_(names),
                UserDepartmentRelation.is_active == True,  # noqa: E712
            )
            .order_by(
                UserDepartmentRelation.user_name,
                UserDepartmentRelation.is_primary.desc(),
                UserDepartmentRelation.id,
            )
        ).all()

        result: dict[str, str] = {}
        for r in rows:
            if not r.user_name or not r.department_id:
                continue
            if r.user_name not in result:
                result[r.user_name] = r.department_id
        return result


user_department_relation_repo = UserDepartmentRelationRepo()

