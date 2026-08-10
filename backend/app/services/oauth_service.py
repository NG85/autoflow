import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from cachetools import TTLCache
import requests

from app.core.config import settings
from app.services.oauth_http import post_json

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
        self._roles_permissions_cache: TTLCache = TTLCache(maxsize=256, ttl=60)
        self._data_scope_cache: TTLCache = TTLCache(maxsize=256, ttl=60)
        self._subordinate_chain_cache: TTLCache = TTLCache(maxsize=256, ttl=60)

    def _permission_request_headers(self) -> Optional[Dict[str, str]]:
        token = (settings.OAUTH_PERMISSION_API_TOKEN or "").strip()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}

    def _post_permission_json(
        self,
        *,
        operation: str,
        path: str,
        json_body: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        return post_json(
            self._session,
            base_url=self._base_url,
            operation=operation,
            path=path,
            json_body=json_body,
            headers=self._permission_request_headers(),
            timeout_seconds=timeout_seconds,
        )

    def query_user_roles_and_permissions(self, *, user_id: UUID, timeout_seconds: int = 30) -> Dict[str, Any]:
        """
        POST /permission/query

        返回结构（失败兜底）：
        {
            "roles": List[Any],
            "permissions": List[str]
        }

        Results are cached per user_id with a 60-second TTL to reduce
        external HTTP calls on high-frequency endpoints like /me/menu-config.
        Failures are NOT cached so the next call retries immediately.
        """
        cache_key = str(user_id)
        cached_result = self._roles_permissions_cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        data = self._post_permission_json(
            operation="permission_query",
            path="/permission/query",
            json_body={"user_id": str(user_id)},
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            logger.error("OAuth permission/query failed, user_id=%s", user_id)
            return {"roles": [], "permissions": []}

        raw = data.get("result", {}) if isinstance(data, dict) else {}
        result = {"roles": raw.get("roles", []), "permissions": raw.get("permissions", [])}
        self._roles_permissions_cache[cache_key] = result
        return result

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
        payload: Dict[str, Any] = {"include_leader_identity": include_leader_identity}
        if level is not None:
            if level < 0:
                logger.error("OAuth departments/leaders invalid level (must be >= 0): %s", level)
            else:
                payload["level"] = level
        if root_department_id:
            payload["root_department_id"] = root_department_id

        data = post_json(
            self._session,
            base_url=self._base_url,
            operation="departments_leaders",
            path="/organization/departments/leaders",
            json_body=payload,
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            return {}

        if data.get("code") != 0:
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

        max_levels=1：OAuth 优先返回主部门 leader；主部门无 leader 时 fallback 上级部门主管。

        返回值保持与 platform_notification_service 历史逻辑一致：已做简化后的 leaders 列表。
        """
        if not base_user_id:
            return []

        data = self._post_permission_json(
            operation="reporting_chain_query",
            path="/permission/reporting-chain/query",
            json_body={
                "userId": base_user_id,
                "maxLevels": max_levels,
                "includeLeaderIdentity": include_leader_identity,
            },
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            logger.error("OAuth reporting-chain/query failed, userId=%s", base_user_id)
            return []

        if data.get("code") != 0:
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
        data = self._post_permission_json(
            operation="users_by_permission",
            path="/permission/users/by-permission",
            json_body={
                "permission": permission,
                "roleCodes": role_codes,
                "includeIdentity": include_identity,
            },
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            logger.error("OAuth users/by-permission failed, permission=%s", permission)
            return []

        if data.get("code") != 0:
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
                    "userId": user.get("userId") or user.get("user_id") or "",
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
        成功结果按 user_id 缓存 60s（列表 data-scope 与 batch-check context 共用）。
        """
        cache_key = f"{user_id}:{int(include_subordinate_identity)}"
        cached = self._subordinate_chain_cache.get(cache_key)
        if cached is not None:
            return cached

        data = self._post_permission_json(
            operation="subordinate_chain_query",
            path="/permission/subordinate-chain/query",
            json_body={
                "user_id": str(user_id),
                "include_subordinate_identity": include_subordinate_identity,
            },
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            logger.error("OAuth subordinate-chain/query failed, user_id=%s", user_id)
            return {}

        if data.get("code") != 0:
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

        parsed = {
            "user_id": result.get("userId"),
            "crm_user_id": result.get("crmUserId"),
            "department_id": result.get("primaryDepartmentId"),
            "department_name": result.get("primaryDepartmentName"),
            "subordinates": simplified_subordinates,
            "raw": result,
        }
        self._subordinate_chain_cache[cache_key] = parsed
        return parsed

    @staticmethod
    def _permission_check_denied() -> Dict[str, Any]:
        return {
            "allowed": False,
            "function_allowed": False,
            "data_allowed": False,
            "effect": "DENY",
            "grant_sources": [],
            "field_mask": [],
            "requires_audit": False,
        }

    @classmethod
    def _parse_permission_check_result(cls, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return cls._permission_check_denied()

        def _bool(key: str, alt: str) -> bool:
            if key in raw:
                return bool(raw[key])
            if alt in raw:
                return bool(raw[alt])
            return False

        allowed = _bool("allowed", "allowed")
        return {
            "allowed": allowed,
            "function_allowed": _bool("function_allowed", "functionAllowed") or allowed,
            "data_allowed": _bool("data_allowed", "dataAllowed"),
            "effect": str(raw.get("effect") or ("ALLOW" if allowed else "DENY")),
            "grant_sources": raw.get("grant_sources") or raw.get("grantSources") or [],
            "field_mask": raw.get("field_mask") or raw.get("fieldMask") or [],
            "requires_audit": _bool("requires_audit", "requiresAudit"),
        }

    def check_permission(
        self,
        *,
        user_id: UUID,
        permission: str,
        crm_user_id: Optional[str] = None,
        resource: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        """
        POST /permission/check — 功能层 + 数据层鉴权（W2/W4）。

        无 resource 时等同 W1 功能门控；带 context 的结果不做本地缓存。
        """
        denied = self._permission_check_denied()
        target_permission = str(permission or "").strip()
        if not target_permission:
            logger.info("OAuth permission/check skipped: empty permission, user_id=%s", user_id)
            return denied

        json_body: Dict[str, Any] = {
            "user_id": str(user_id),
            "permission": target_permission,
        }
        if crm_user_id:
            json_body["crm_user_id"] = str(crm_user_id)
        if resource:
            json_body["resource"] = resource
        if context:
            json_body["context"] = context

        data = self._post_permission_json(
            operation="permission_check",
            path="/permission/check",
            json_body=json_body,
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            logger.error(
                "OAuth permission/check transport failed, user_id=%s, permission=%s",
                user_id,
                target_permission,
            )
            return denied

        if data.get("code") != 0:
            logger.error(
                "OAuth permission/check returned error: user_id=%s, permission=%s, body=%s",
                user_id,
                target_permission,
                data,
            )
            return denied

        result = self._parse_permission_check_result(data.get("result"))
        logger.info(
            "OAuth permission/check user_id=%s permission=%s allowed=%s effect=%s",
            user_id,
            target_permission,
            result["allowed"],
            result["effect"],
        )
        return result

    def check_function_permission(
        self,
        *,
        user_id: UUID,
        permission: str,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        """
        POST /permission/check — W1 功能层鉴权（矩阵 RBAC）。

        返回 OAuth PermissionCheckVO 字段（camelCase 已由 post_json 层处理为 snake 或保持原样）。
        失败时 allowed=false。
        """
        return self.check_permission(
            user_id=user_id,
            permission=permission,
            timeout_seconds=timeout_seconds,
        )

    def get_data_scope(
        self,
        *,
        user_id: UUID,
        entity: str,
        crm_user_id: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        """
        POST /permission/data-scope — 列表数据范围（W3/W4）。

        响应本地缓存 60s（按 user_id + entity）。
        """
        denied: Dict[str, Any] = {"entity": entity, "merge": "OR", "filters": []}
        target_entity = str(entity or "").strip()
        if not target_entity:
            logger.info("OAuth permission/data-scope skipped: empty entity, user_id=%s", user_id)
            return denied

        cache_key = f"{user_id}:{target_entity}"
        cached = self._data_scope_cache.get(cache_key)
        if cached is not None:
            return cached

        json_body: Dict[str, Any] = {
            "user_id": str(user_id),
            "entity": target_entity,
        }
        if crm_user_id:
            json_body["crm_user_id"] = str(crm_user_id)

        data = self._post_permission_json(
            operation="permission_data_scope",
            path="/permission/data-scope",
            json_body=json_body,
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            logger.error(
                "OAuth permission/data-scope transport failed, user_id=%s, entity=%s",
                user_id,
                target_entity,
            )
            return denied

        if data.get("code") != 0:
            logger.error(
                "OAuth permission/data-scope returned error: user_id=%s, entity=%s, body=%s",
                user_id,
                target_entity,
                data,
            )
            return denied

        raw = data.get("result") if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            return denied

        result = {
            "entity": raw.get("entity") or target_entity,
            "merge": raw.get("merge") or "OR",
            "filters": raw.get("filters") if isinstance(raw.get("filters"), list) else [],
        }
        if not result["filters"]:
            logger.warning(
                "OAuth permission/data-scope returned empty filters (list will deny-all), "
                "user_id=%s, entity=%s, base_url=%s",
                user_id,
                target_entity,
                self._base_url,
            )
        self._data_scope_cache[cache_key] = result
        return result

    def batch_check_permissions(
        self,
        *,
        user_id: UUID,
        checks: List[Dict[str, Any]],
        crm_user_id: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        POST /permission/batch-check — 批量鉴权（当前页行内按钮等）。

        返回与 checks 顺序一致的结果列表；失败时全部 deny。
        """
        denied_item = {
            "allowed": False,
            "function_allowed": False,
            "data_allowed": False,
            "effect": "DENY",
            "grant_sources": [],
        }
        if not checks:
            return []

        json_body: Dict[str, Any] = {
            "user_id": str(user_id),
            "checks": checks,
        }
        if crm_user_id:
            json_body["crm_user_id"] = str(crm_user_id)

        data = self._post_permission_json(
            operation="permission_batch_check",
            path="/permission/batch-check",
            json_body=json_body,
            timeout_seconds=timeout_seconds,
        )
        if data is None:
            logger.error("OAuth permission/batch-check transport failed, user_id=%s", user_id)
            return [{**denied_item, "permission": item.get("permission", "")} for item in checks]

        if data.get("code") != 0:
            logger.error("OAuth permission/batch-check returned error: user_id=%s, body=%s", user_id, data)
            return [{**denied_item, "permission": item.get("permission", "")} for item in checks]

        raw = data.get("result") if isinstance(data, dict) else {}
        results = raw.get("results") if isinstance(raw, dict) else None
        if not isinstance(results, list):
            return [{**denied_item, "permission": item.get("permission", "")} for item in checks]

        parsed: List[Dict[str, Any]] = []
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                permission = checks[index].get("permission", "") if index < len(checks) else ""
                parsed.append({**denied_item, "permission": permission})
                continue
            allowed = bool(item.get("allowed"))
            parsed.append(
                {
                    "permission": item.get("permission") or (
                        checks[index].get("permission", "") if index < len(checks) else ""
                    ),
                    "allowed": allowed,
                    "function_allowed": bool(item.get("function_allowed", item.get("functionAllowed", allowed))),
                    "data_allowed": bool(item.get("data_allowed", item.get("dataAllowed", allowed))),
                    "effect": str(item.get("effect") or ("ALLOW" if allowed else "DENY")),
                    "grant_sources": item.get("grant_sources") or item.get("grantSources") or [],
                }
            )
        return parsed

    def check_user_has_permission(self, *, user_id: UUID, permission: str) -> bool:
        """
        检查用户是否具有指定权限

        Args:
            user_id: 用户ID
            permission: 权限名称，如 ``sales:follow_up:view``

        Returns:
            bool: 是否具有该权限
        """
        roles_and_permissions = self.query_user_roles_and_permissions(user_id=user_id)
        permissions = roles_and_permissions.get("permissions", [])
        if not isinstance(permissions, list):
            permissions = []

        target_permission = str(permission or "").strip()
        if not target_permission:
            logger.info(f"User {user_id} permission check skipped due to empty permission")
            return False

        normalized_permissions = {
            str(p).strip()
            for p in permissions
            if p is not None and str(p).strip()
        }
        has_permission = target_permission in normalized_permissions
        logger.info(f"User {user_id} permission check for {permission}: {has_permission}")
        return has_permission

    def check_user_has_role(self, *, user_id: UUID, role: str) -> bool:
        """
        检查用户是否具有指定角色
        """
        roles_and_permissions = self.query_user_roles_and_permissions(user_id=user_id)
        roles = roles_and_permissions.get("roles", [])
        if not isinstance(roles, list):
            roles = []

        target_role = str(role or "").strip().lower()
        if not target_role:
            logger.info(f"User {user_id} role check skipped due to empty role")
            return False

        # 用role的code属性来匹配
        role_codes = {
            str(r.get("code", "")).strip().lower()
            for r in roles
            if isinstance(r, dict) and r.get("code")
        }
        has_role = target_role in role_codes
        logger.info(f"User {user_id} role check for {role}: {has_role}")
        return has_role


oauth_client = OAuthClient()
