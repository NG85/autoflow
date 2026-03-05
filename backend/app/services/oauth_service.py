import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

# from cachetools import TTLCache, cached
# from cachetools.keys import methodkey
import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


class OAuthClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._base_url = (base_url or settings.OAUTH_BASE_URL).rstrip("/")
        self._session = session or requests.Session()


    # @cached(cache=TTLCache(maxsize=100, ttl=60 * 10), key=methodkey)
    def query_user_roles_and_permissions(self, *, user_id: UUID, timeout_seconds: int = 30) -> Dict[str, Any]:
        """
        POST /permission/query

        返回结构（失败兜底）：
        {
            "roles": List[Any],
            "permissions": List[str]
        }
        """
        url = f"{self._base_url}/permission/query"
        try:
            resp = self._session.post(
                url,
                json={"user_id": str(user_id)},
                headers={"Content-Type": "application/json"},
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {}) if isinstance(data, dict) else {}
            return {"roles": result.get("roles", []), "permissions": result.get("permissions", [])}
        except Exception:
            logger.exception("OAuth permission/query failed, user_id=%s", user_id)
            return {"roles": [], "permissions": []}

    def get_departments_with_leaders(
        self,
        *,
        level: Optional[int] = None,
        root_department_id: Optional[str] = None,
        group_by_first_level_department: bool = False,
        include_leader_identity: bool = True,
        timeout_seconds: int = 30,
    ) -> Dict[str, Optional[List[Dict[str, Any]]]]:
        """
        POST /organization/departments/leaders

        新增可选参数（不传则返回全部）：
        - level: 按层级过滤部门（ge=0）
            - root_department_id 未指定时：level 为全局层级，根部门 level=1
            - root_department_id 指定时：level 为相对层级，level=1 表示根部门的直接子部门，level=0 表示根部门本身
        - root_department_id: 作为层级计算的根部门ID（对应 department_mirror.unique_id）
        - group_by_first_level_department: 是否按一级部门分组聚合返回
            - False（默认）：平铺返回，每个 key 为 department_name
            - True：按 path 的第一级部门聚合，同一一级部门下的子部门 leaders 合并到同一 key

        返回结构：
        - key: department_name
        - value: managers list 或 None
        """
        url = f"{self._base_url}/organization/departments/leaders"
        payload: Dict[str, Any] = {"include_leader_identity": include_leader_identity}
        if level is not None:
            if level < 0:
                logger.error("OAuth departments/leaders invalid level (must be >= 0): %s", level)
            else:
                payload["level"] = level
        if root_department_id:
            payload["root_department_id"] = root_department_id

        try:
            resp = self._session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()

            if not isinstance(data, dict) or data.get("code") != 0:
                logger.error("OAuth departments/leaders returned error: %s", data)
                return {}

            result = data.get("result", [])
            if not isinstance(result, list):
                logger.error("OAuth departments/leaders invalid result format: %s", result)
                return {}

            if group_by_first_level_department:
                # deptId/path -> deptName lookup for resolving first-level group name
                dept_id_to_name: Dict[str, str] = {}
                path_to_name: Dict[str, str] = {}
                for dept_info in result:
                    if not isinstance(dept_info, dict):
                        continue
                    dept_name = (dept_info.get("departmentName") or "").strip()
                    if not dept_name:
                        continue
                    dept_id = (str(dept_info.get("departmentId")) if dept_info.get("departmentId") is not None else "").strip()
                    if dept_id:
                        dept_id_to_name[dept_id] = dept_name
                    dept_path = (dept_info.get("path") or "").strip()
                    if dept_path:
                        path_to_name[dept_path] = dept_name

                grouped: Dict[str, List[Dict[str, Any]]] = {}
                grouped_seen: Dict[str, set] = {}

                for dept_info in result:
                    if not isinstance(dept_info, dict):
                        continue
                    department_name = dept_info.get("departmentName")
                    if not department_name:
                        continue
                    dept_path = (dept_info.get("path") or "").strip()
                    first_seg = dept_path.split("/", 1)[0] if dept_path else ""

                    group_name = ""
                    if first_seg:
                        group_name = (
                            path_to_name.get(first_seg)
                            or dept_id_to_name.get(first_seg)
                            or first_seg
                        )
                    else:
                        # If path missing, fallback to the department itself
                        group_name = str(department_name)

                    leaders = dept_info.get("leaders", []) or []
                    if not isinstance(leaders, list) or not leaders:
                        grouped.setdefault(group_name, [])
                        grouped_seen.setdefault(group_name, set())
                        continue

                    bucket = grouped.setdefault(group_name, [])
                    seen = grouped_seen.setdefault(group_name, set())

                    for leader in leaders:
                        if not isinstance(leader, dict):
                            continue
                        manager = {
                            "open_id": leader.get("openId"),
                            "name": leader.get("name", "") or "",
                            "crmUserId": leader.get("crmUserId", "") or "",
                            "userId": leader.get("userId", "") or "",
                            "platform": leader.get("platform", "feishu"),
                            "type": "department_manager",
                            # 保留 leader 的来源部门，避免聚合后丢上下文
                            "department": department_name,
                            "receive_id_type": "open_id",
                        }

                        # Reasonable per-group dedupe (openId/userId/crmUserId/uid/askUserId)
                        platform = str(manager.get("platform") or "")
                        ident = (
                            leader.get("openId")
                            or leader.get("userId")
                            or leader.get("crmUserId")
                            or leader.get("uid")
                            or leader.get("askUserId")
                            or ""
                        )
                        key = f"{platform}:{ident}" if ident else f"{platform}:{manager.get('name')}"
                        if key in seen:
                            continue
                        seen.add(key)
                        bucket.append(manager)

                grouped_final: Dict[str, Optional[List[Dict[str, Any]]]] = {}
                for k, v in grouped.items():
                    grouped_final[k] = v or None

                logger.info(
                    "OAuth departments/leaders loaded (grouped): %s first-level departments",
                    len(grouped_final),
                )
                return grouped_final

            departments_with_managers: Dict[str, Optional[List[Dict[str, Any]]]] = {}
            for dept_info in result:
                if not isinstance(dept_info, dict):
                    continue
                department_name = dept_info.get("departmentName")
                if not department_name:
                    continue

                leaders = dept_info.get("leaders", []) or []
                if not leaders:
                    departments_with_managers[department_name] = None
                    continue

                manager_list: List[Dict[str, Any]] = []
                for leader in leaders:
                    if not isinstance(leader, dict):
                        continue
                    manager_list.append(
                        {
                            "open_id": leader.get("openId"),
                            "name": leader.get("name", "") or "",
                            "crmUserId": leader.get("crmUserId", "") or "",
                            "userId": leader.get("userId", "") or "",
                            "platform": leader.get("platform", "feishu"),
                            "type": "department_manager",
                            "department": department_name,
                            "receive_id_type": "open_id",
                        }
                    )
                departments_with_managers[department_name] = manager_list

            logger.info("OAuth departments/leaders loaded: %s departments", len(departments_with_managers))
            return departments_with_managers
        except Exception:
            logger.exception("OAuth departments/leaders failed")
            return {}

    def get_reporting_chain_leaders(
        self,
        *,
        base_user_id: str,
        max_levels: int = 2,
        include_leader_identity: bool = True,
        timeout_seconds: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        POST /permission/reporting-chain/query

        返回值保持与 platform_notification_service 历史逻辑一致：已做简化后的 leaders 列表。
        """
        if not base_user_id:
            return []

        url = f"{self._base_url}/permission/reporting-chain/query"
        payload = {
            "userId": base_user_id,
            "maxLevels": max_levels,
            "includeLeaderIdentity": include_leader_identity,
        }

        try:
            resp = self._session.post(url, json=payload, timeout=timeout_seconds)
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception:
            logger.exception("OAuth reporting-chain/query failed, userId=%s", base_user_id)
            return []

        if not isinstance(data, dict) or data.get("code") != 0:
            logger.error("OAuth reporting-chain/query returned error: %s", data)
            return []

        result = data.get("result") or {}
        leaders = result.get("leaders") or []

        simplified: List[Dict[str, Any]] = []
        for leader in leaders:
            if not isinstance(leader, dict):
                continue
            platform = leader.get("platform")
            open_id = leader.get("openId") or leader.get("open_id")
            if not platform or not open_id:
                continue
            simplified.append(
                {
                    "open_id": open_id,
                    "name": leader.get("name") or "Unknown",
                    "type": "leader",
                    "department": leader.get("department") or "部门团队",
                    "receive_id_type": "open_id",
                    "platform": platform,
                }
            )

        return simplified

    def get_users_by_permission(
        self,
        *,
        permission: str,
        role_codes: Optional[List[str]] = None,
        include_identity: bool = True,
        timeout_seconds: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        POST /permission/users/by-permission

        返回值保持与 platform_notification_service 历史逻辑一致：已做简化后的 users 列表。
        """
        url = f"{self._base_url}/permission/users/by-permission"
        payload = {"permission": permission, "roleCodes": role_codes, "includeIdentity": include_identity}

        try:
            resp = self._session.post(url, json=payload, timeout=timeout_seconds)
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception:
            logger.exception("OAuth users/by-permission failed, permission=%s", permission)
            return []

        if not isinstance(data, dict) or data.get("code") != 0:
            logger.error("OAuth users/by-permission returned error: %s", data)
            return []

        users = data.get("result") or []
        simplified: List[Dict[str, Any]] = []
        for user in users:
            if not isinstance(user, dict):
                continue
            platform = user.get("platform")
            open_id = user.get("openId") or user.get("open_id")
            if not platform or not open_id:
                continue
            simplified.append(
                {
                    "name": user.get("name") or "Unknown",
                    "platform": platform,
                    "open_id": open_id,
                    "crm_user_id": user.get("crmUserId"),
                    "raw": user,
                }
            )
        return simplified

    def get_subordinate_chain(
        self,
        *,
        user_id: UUID,
        include_subordinate_identity: bool = True,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        """
        POST /permission/subordinate-chain/query

        返回简化的下属列表（含简化后的 subordinates + raw 兜底）。
        """
        url = f"{self._base_url}/permission/subordinate-chain/query"
        payload = {
            "user_id": str(user_id),
            "include_subordinate_identity": include_subordinate_identity,
        }
        headers: Dict[str, str] = {"Content-Type": "application/json"}

        try:
            resp = self._session.post(url, json=payload, headers=headers, timeout=timeout_seconds)
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception:
            logger.exception("OAuth subordinate-chain/query failed, user_id=%s", user_id)
            return {}

        if not isinstance(data, dict) or data.get("code") != 0:
            logger.error("OAuth subordinate-chain/query returned error: %s", data)
            return {}

        result = data.get("result")
        if not isinstance(result, dict):
            return {}

        subordinates = result.get("subordinates") or []
        if not isinstance(subordinates, list):
            return {}

        simplified_subordinates: List[Dict[str, Any]] = []
        for item in subordinates:
            if not isinstance(item, dict):
                continue
            simplified_subordinates.append(
                {
                    "user_id": item.get("userId"),
                    "crm_user_id": item.get("crmUserId"),
                    "name": item.get("name") or "",
                    "department_name": item.get("department"),
                    "raw": item,
                }
            )

        return {
            "user_id": result.get("userId"),
            "crm_user_id": result.get("crmUserId"),
            "department_id": result.get("primaryDepartmentId"),
            "department_name": result.get("primaryDepartmentName"),
            "subordinates": simplified_subordinates,
            "raw": result,
        }


    def check_user_has_permission(self, *, user_id: UUID, permission: str) -> bool:
        """
        检查用户是否具有指定权限
        
        Args:
            user_id: 用户ID
            permission: 权限名称，如 "report51:company:view" 或 "report51:dept:view"
            
        Returns:
            bool: 是否具有该权限
        """
        roles_and_permissions = self.query_user_roles_and_permissions(user_id=user_id)
        permissions = roles_and_permissions.get("permissions", [])
        has_permission = permission in permissions
        logger.info(f"User {user_id} permission check for {permission}: {has_permission}")
        return has_permission


    def check_user_has_role(self, *, user_id: UUID, role: str) -> bool:
        """
        检查用户是否具有指定角色
        """
        roles_and_permissions = self.query_user_roles_and_permissions(user_id=user_id)
        roles = roles_and_permissions.get("roles", [])
        # 用role的code属性来匹配
        role_codes = [r.get("code") for r in roles if isinstance(r, dict) and r.get("code")]
        has_role = role.lower() in [rc.lower() for rc in role_codes]
        logger.info(f"User {user_id} role check for {role}: {has_role}")
        return has_role
    
    
oauth_client = OAuthClient()

