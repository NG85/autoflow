from __future__ import annotations

from typing import Optional, Iterable

from sqlmodel import Session, select

from app.models.department_mirror import DepartmentMirror
from app.repositories.base_repo import BaseRepo


class DepartmentMirrorRepo(BaseRepo):
    model_cls = DepartmentMirror

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


