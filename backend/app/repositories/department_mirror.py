from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from app.models.department_mirror import DepartmentMirror
from app.repositories.base_repo import BaseRepo


class DepartmentMirrorRepo(BaseRepo):
    model_cls = DepartmentMirror

    def get_ancestor_chains_bulk(
        self,
        db_session: Session,
        department_ids: Iterable[str],
    ) -> Dict[str, List[Tuple[str, str]]]:
        """
        批量获取部门祖先链（含自身），从自身到根的顺序。

        支持多棵树：mirror 里可有多个根节点（parent_id 为空），每条链在各自树的根结束，
        不要求所有部门归到同一公共根，不会因此报错。若存在环则用 visited 提前退出，不死循环。

        返回: dept_id -> [(dept_id, dept_name), ...]，链中第一个为自身，最后为该树根。
        若部门不在 mirror 中或未激活，该 id 仍会出现在返回中且链仅含 (id, "未知部门")。
        """
        ids = [str(x).strip() for x in (department_ids or []) if x and str(x).strip()]
        if not ids:
            return {}

        rows = db_session.exec(
            select(
                DepartmentMirror.unique_id,
                DepartmentMirror.parent_id,
                DepartmentMirror.department_name,
            ).where(
                DepartmentMirror.is_active == True,
            )
        ).all()

        # id -> (parent_id, department_name)，key 统一 strip 便于查找
        node_by_id: Dict[str, Tuple[Optional[str], str]] = {}
        for uid, parent_id, name in rows:
            if not uid:
                continue
            uid_s = str(uid).strip()
            node_by_id[uid_s] = (
                str(parent_id).strip() if parent_id else None,
                (name or "").strip() or "未知部门",
            )

        result: Dict[str, List[Tuple[str, str]]] = {}
        for did in ids:
            chain: List[Tuple[str, str]] = []
            current_id = did
            visited: set[str] = set()
            # 从当前部门沿 parent_id 向上遍历，每一层上级都会加入 chain
            while current_id:
                if current_id in visited:
                    break
                visited.add(current_id)
                parent_id, dept_name = node_by_id.get(
                    current_id, (None, "未知部门")
                )
                chain.append((current_id, dept_name))
                current_id = (parent_id or "").strip() if parent_id else ""
            result[did] = chain
        return result

    def get_subtree_department_ids(
        self,
        db_session: Session,
        department_id: str,
    ) -> List[str]:
        """
        返回指定部门及其所有子部门（后代）的 unique_id 列表（含自身）。
        用于按“本部门+子部门”维度查询数据。若部门不在 mirror 中，仅返回 [department_id]。
        """
        did = (department_id or "").strip()
        if not did:
            return []

        rows = db_session.exec(
            select(
                DepartmentMirror.unique_id,
                DepartmentMirror.parent_id,
            ).where(
                DepartmentMirror.is_active == True,
            )
        ).all()

        node_by_id: Dict[str, Optional[str]] = {}
        for uid, parent_id in rows:
            if not uid:
                continue
            uid_s = str(uid).strip()
            node_by_id[uid_s] = str(parent_id).strip() if parent_id else None

        if did not in node_by_id:
            return [did]

        # parent_id -> list of children
        children_by_parent: Dict[str, List[str]] = {}
        for uid, parent_id in node_by_id.items():
            if parent_id:
                children_by_parent.setdefault(parent_id, []).append(uid)

        subtree: List[str] = [did]
        stack: List[str] = [did]
        while stack:
            n = stack.pop()
            for c in children_by_parent.get(n, []):
                subtree.append(c)
                stack.append(c)
        return subtree

    def get_department_ids_by_name(
        self,
        db_session: Session,
        department_name: str,
    ) -> List[str]:
        """按部门名称查询所有匹配的部门 unique_id（同名可能存在于多棵树）。"""
        name = (department_name or "").strip()
        if not name:
            return []
        rows = db_session.exec(
            select(DepartmentMirror.unique_id).where(
                DepartmentMirror.department_name == name,
                DepartmentMirror.is_active == True,
            )
        ).all()
        return [str(uid).strip() for uid in rows if uid]

    def get_department_name_by_id(self, db_session: Session, department_id: str) -> Optional[str]:
        if not department_id:
            return None
        return db_session.exec(
            select(DepartmentMirror.department_name).where(
                DepartmentMirror.unique_id == department_id,
                DepartmentMirror.is_active == True,
            )
        ).first()

    def get_department_names_by_ids(self, db_session: Session, department_ids: Iterable[str]) -> dict[str, str]:
        ids = [x for x in (department_ids or []) if x]
        if not ids:
            return {}
        rows = db_session.exec(
            select(DepartmentMirror.unique_id, DepartmentMirror.department_name).where(
                DepartmentMirror.unique_id.in_(ids),
                DepartmentMirror.is_active == True,
            )
        ).all()
        return {uid: name for uid, name in rows if uid and name}


department_mirror_repo = DepartmentMirrorRepo()


