from typing import Any

from fastapi import APIRouter
from sqlmodel import select

from app.api.deps import CurrentUserDep, SessionDep
from app.models.user_department_relation import UserDepartmentRelation
from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client
from app.site_settings import SiteSetting

router = APIRouter()


def _extract_role_ids(raw_roles: Any) -> set[str]:
    role_ids: set[str] = set()
    if not isinstance(raw_roles, list):
        return role_ids
    for role in raw_roles:
        if not isinstance(role, dict):
            continue
        # oauth permission/query returns roles with `code` (see oauth_service.check_user_has_role)
        role_code = role.get("code")
        if role_code is None:
            continue
        role_code_str = str(role_code).strip()
        if role_code_str:
            role_ids.add(role_code_str)
    return role_ids


def _extract_permission_ids(raw_permissions: Any) -> set[str]:
    if not isinstance(raw_permissions, list):
        return set()
    return {str(p).strip() for p in raw_permissions if isinstance(p, str) and p.strip()}


def _extract_department_ids(session: SessionDep, user_id: Any) -> set[str]:
    crm_user_id = user_profile_repo.get_crm_user_id_by_user_id(session, user_id)
    if not crm_user_id:
        return set()

    rows = session.exec(
        select(UserDepartmentRelation.department_id).where(
            UserDepartmentRelation.crm_user_id == str(crm_user_id),
            UserDepartmentRelation.is_active == True,  # noqa: E712
        )
    ).all()
    return {str(department_id).strip() for department_id in rows if department_id}


def _is_menu_visible_for_user(
    menu: dict[str, Any],
    *,
    user_id: str,
    role_ids: set[str],
    permission_ids: set[str],
    department_ids: set[str],
) -> bool:
    if not bool(menu.get("visible", False)):
        return False

    audience = menu.get("audience")
    if not isinstance(audience, dict):
        return True

    mode = audience.get("mode", "all")
    if mode == "all":
        return True
    if mode != "allowlist":
        return False

    roles_any = {
        str(role_id).strip()
        for role_id in audience.get("roles_any", [])
        if str(role_id).strip()
    }
    permissions_any = {
        str(perm).strip()
        for perm in audience.get("permissions_any", [])
        if str(perm).strip()
    }
    user_ids_any = {
        str(uid).strip() for uid in audience.get("user_ids_any", []) if str(uid).strip()
    }
    department_ids_any = {
        str(dept_id).strip()
        for dept_id in audience.get("department_ids_any", [])
        if str(dept_id).strip()
    }

    return bool(
        (roles_any and role_ids.intersection(roles_any))
        or (permissions_any and permission_ids.intersection(permissions_any))
        or (user_ids_any and user_id in user_ids_any)
        or (department_ids_any and department_ids.intersection(department_ids_any))
    )


def _prune_orphan_children(menus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove children whose parent_key references a menu not in the list.
    Iterates until stable, so grandchildren of removed parents are also pruned."""
    result = list(menus)
    changed = True
    while changed:
        present_keys = {m.get("key") for m in result if m.get("key")}
        before = len(result)
        result = [
            m for m in result
            if not m.get("parent_key") or m.get("parent_key") in present_keys
        ]
        changed = len(result) < before
    return result


@router.get("/me/menu-config")
def my_menu_config(session: SessionDep, user: CurrentUserDep) -> dict[str, Any]:
    menu_config = SiteSetting.get_setting("menu_config")
    if not isinstance(menu_config, dict):
        return {"version": 1, "menus": []}

    menus = menu_config.get("menus")
    if not isinstance(menus, list):
        return {"version": 1, "menus": []}

    user_id = str(user.id)
    roles_and_permissions = oauth_client.query_user_roles_and_permissions(user_id=user.id)
    role_ids = _extract_role_ids(roles_and_permissions.get("roles"))
    permission_ids = _extract_permission_ids(roles_and_permissions.get("permissions"))
    department_ids = _extract_department_ids(session, user.id)

    visible_menus = [
        menu
        for menu in menus
        if isinstance(menu, dict)
        and _is_menu_visible_for_user(
            menu,
            user_id=user_id,
            role_ids=role_ids,
            permission_ids=permission_ids,
            department_ids=department_ids,
        )
    ]

    filtered_menus = _prune_orphan_children(visible_menus)

    result = dict(menu_config)
    result["menus"] = filtered_menus
    return result
