from typing import Any, Dict, Optional, List
from datetime import datetime
import logging
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import aliased
from sqlmodel import Session, select, func, desc, asc
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import paginate
from uuid import UUID
from zoneinfo import ZoneInfo

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_accounts import CRMAccount
from app.api.routes.crm.models import (
    VisitAttachment,
    VisitRecordQueryRequest,
    VisitRecordResponse,
)
from app.policies.visit_record_access import VisitRecordAccessPolicy
from app.repositories.crm_account import crm_account_repo
from app.repositories.base_repo import BaseRepo
from app.repositories.user_profile import user_profile_repo
from app.repositories.user_department_relation import user_department_relation_repo
from app.repositories.department_mirror import department_mirror_repo
from app.services.crm_config_service import get_resolved_field_mapping
from app.services.oauth_service import oauth_client
from app.utils.crm_account_tags import parse_account_tags
from app.utils.crm_followup_object_type import resolve_customer_attribute_display_label
from app.utils.date_utils import convert_beijing_date_to_utc_range

logger = logging.getLogger(__name__)

_VISIT_RECORD_FILTER_OPTION_SEP = "\x1e"

# AI 评估质量等级（filter-options 固定枚举，与 save_engine 评估结果一致）
_VISIT_QUALITY_LEVELS_ZH: tuple[str, ...] = ("不合格", "合格", "优秀")
_VISIT_QUALITY_LEVELS_EN: tuple[str, ...] = ("unqualified", "qualified", "excellent")
_VISIT_RECORD_QUALITY_FILTER_OPTIONS: Dict[str, List[str]] = {
    "followup_quality_levels_zh": list(_VISIT_QUALITY_LEVELS_ZH),
    "followup_quality_levels_en": list(_VISIT_QUALITY_LEVELS_EN),
    "next_steps_quality_levels_zh": list(_VISIT_QUALITY_LEVELS_ZH),
    "next_steps_quality_levels_en": list(_VISIT_QUALITY_LEVELS_EN),
}

# filter-options：响应字段名 -> crm_sales_visit_records 列名
_VISIT_RECORD_BASE_FILTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("recorders", "recorder"),
    ("departments", "recorder_department_name"),
)
_VISIT_RECORD_SIMPLE_FILTER_FIELDS: tuple[tuple[str, str], ...] = (("subjects", "subject"),)
_VISIT_RECORD_COMPLETE_FILTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("communication_methods", "visit_communication_method"),
    ("visit_purposes", "visit_purpose"),
    ("visit_types", "visit_type"),
)


def _split_group_concat(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part for part in value.split(_VISIT_RECORD_FILTER_OPTION_SEP) if part]


def _followup_object_crm_account_join():
    """
    跟进对象与客户/伙伴二选一：等级、extra 分别从 account_id / partner_id 关联 crm_accounts，再 coalesce。
    """
    account_crm = aliased(CRMAccount, name="followup_object_account_crm")
    partner_crm = aliased(CRMAccount, name="followup_object_partner_crm")
    customer_level_col = func.coalesce(account_crm.customer_level, partner_crm.customer_level)
    customer_attribute_col = func.coalesce(
        account_crm.customer_attribute, partner_crm.customer_attribute
    )
    followup_extra_col = func.coalesce(account_crm.extra, partner_crm.extra)
    return account_crm, partner_crm, customer_level_col, customer_attribute_col, followup_extra_col


def _resolve_followup_object_name(
    account_name: Optional[str],
    partner_name: Optional[str],
) -> Optional[str]:
    name = (account_name or "").strip()
    if name:
        return name
    partner = (partner_name or "").strip()
    return partner or None


def _resolve_followup_object_id(
    account_id: Optional[str],
    partner_id: Optional[str],
) -> Optional[str]:
    aid = (account_id or "").strip()
    if aid:
        return aid
    pid = (partner_id or "").strip()
    return pid or None


def _visit_record_account_id_is_set():
    trimmed = func.trim(CRMSalesVisitRecord.account_id)
    return and_(
        CRMSalesVisitRecord.account_id.isnot(None),
        trimmed != "",
    )


def _visit_record_account_id_is_empty():
    """account_id 为空（含 NULL、空串、仅空白），用于 partner 类跟进对象筛选。"""
    trimmed = func.trim(CRMSalesVisitRecord.account_id)
    return or_(
        CRMSalesVisitRecord.account_id.is_(None),
        trimmed == "",
    )


def _visit_record_partner_id_is_set():
    trimmed = func.trim(CRMSalesVisitRecord.partner_id)
    return and_(
        CRMSalesVisitRecord.partner_id.isnot(None),
        trimmed != "",
    )


def _visit_record_followup_is_partner_type():
    """
    跟进对象为合作伙伴：account 必须为空且 partner 非空。
    历史数据可能 account/partner 同时有值（跟进对象实为 account），不可仅凭 partner 有值命中。
    """
    return and_(
        _visit_record_account_id_is_empty(),
        _visit_record_partner_id_is_set(),
    )


def _customer_attribute_filter_predicate(selected_values: List[str]):
    """
    customer_attribute 仅接受 end_customer / partner 键（与 filter-options 一致），多选 OR。
    - end_customer：account_id 非空（含历史双填，跟进对象以 account 为准）
    - partner：account_id 为空且 partner_id 非空（排除历史双填误命中）
    """
    selected = {value.strip() for value in selected_values if value and value.strip()}
    if not selected:
        return None

    predicates = []
    if "end_customer" in selected:
        predicates.append(_visit_record_account_id_is_set())
    if "partner" in selected:
        predicates.append(_visit_record_followup_is_partner_type())
    if not predicates:
        return None
    return or_(*predicates) if len(predicates) > 1 else predicates[0]


def _empty_visit_record_page(request: VisitRecordQueryRequest) -> Page[VisitRecordResponse]:
    return Page(
        items=[],
        total=0,
        page=request.page,
        size=request.page_size,
        pages=0,
    )


def _fill_external_collaboration_from_legacy_partner(record_dict: Dict[str, Any]) -> None:
    """
    历史兼容：
    旧数据里存在「account_* 与 partner_* 同时有值」但 external_* 为空。
    在新语义中这通常等价于「跟进对象=account，外部协同=partner」。
    仅在响应层补齐 external_*，不改库表原数据。
    """
    account_name = str(record_dict.get("account_name") or "").strip()
    account_id = str(record_dict.get("account_id") or "").strip()
    partner_name = str(record_dict.get("partner_name") or "").strip()
    partner_id = str(record_dict.get("partner_id") or "").strip()
    external_name = str(record_dict.get("external_collaboration_partner_name") or "").strip()
    external_id = str(record_dict.get("external_collaboration_partner_id") or "").strip()

    has_account = bool(account_name or account_id)
    has_partner = bool(partner_name or partner_id)
    has_external = bool(external_name or external_id)

    if has_account and has_partner and not has_external:
        record_dict["external_collaboration_partner_name"] = partner_name or None
        record_dict["external_collaboration_partner_id"] = partner_id or None


def _convert_to_response(
    record: CRMSalesVisitRecord,
    customer_level: Optional[str] = None,
    customer_attribute: Optional[str] = None,
    department: Optional[str] = None,
    followup_extra: Optional[dict] = None,
) -> VisitRecordResponse:
    """
    将CRMSalesVisitRecord转换为VisitRecordResponse
    """
    # 先处理需要转换的字段
    record_dict = record.model_dump()
    _fill_external_collaboration_from_legacy_partner(record_dict)
    
    # 处理UUID字段转换为字符串
    if record.recorder_id:
        record_dict["recorder_id"] = str(record.recorder_id)
    
    # 处理日期字段转换为ISO格式字符串
    if record.visit_communication_date:
        record_dict["visit_communication_date"] = record.visit_communication_date.isoformat()
    if record.last_modified_time:
        # 将UTC时间转换为本地时区字符串
        from app.utils.date_utils import convert_utc_to_local_timezone
        record_dict["last_modified_time"] = convert_utc_to_local_timezone(record.last_modified_time)
    
    # 处理协同参与人字段 - 将JSON数组转换为拼接的name字符串
    from app.utils.participants_utils import format_collaborative_participants_names
    record_dict["collaborative_participants"] = format_collaborative_participants_names(record.collaborative_participants)
    
    attachment = record_dict.get("attachment")
    if attachment:
        record_dict["attachment"] = VisitAttachment.from_legacy_value(attachment)
    
    # 处理联系人字段：如果数据库中有contacts字段则使用，否则从旧字段构造
    from app.api.routes.crm.models import Contact
    contacts_list = None
    if record.contacts and isinstance(record.contacts, list) and len(record.contacts) > 0:
        # 使用新字段（contacts）
        contacts_list = [Contact(**contact) if isinstance(contact, dict) else contact for contact in record.contacts]
    elif record.contact_name or record.contact_position or record.contact_id:
        # 从旧字段构造联系人列表（兼容旧数据）
        contact_dict = {}
        if record.contact_name:
            contact_dict['name'] = record.contact_name
        if record.contact_position:
            contact_dict['position'] = record.contact_position
        if record.contact_id:
            contact_dict['contact_id'] = record.contact_id
        if contact_dict:
            contacts_list = [Contact(**contact_dict)]
    
    # 将contacts字段添加到record_dict中
    if contacts_list:
        record_dict["contacts"] = contacts_list

    # 使用处理后的字典创建VisitRecordResponse
    from app.api.routes.crm.models import AccountTagOptionOut

    response = VisitRecordResponse.model_validate(record_dict)
    
    # 添加关联字段
    response.customer_level = customer_level
    response.customer_attribute = customer_attribute
    response.tags = [
        AccountTagOptionOut(id=tag.id, name=tag.name)
        for tag in parse_account_tags(followup_extra if isinstance(followup_extra, dict) else None)
    ]
    response.department = department  # 拜访人部门
    response.followup_object_name = _resolve_followup_object_name(
        record.account_name, record.partner_name
    )
    response.followup_object_id = _resolve_followup_object_id(
        record.account_id, record.partner_id
    )

    return response


class VisitRecordRepo(BaseRepo):
    model_cls = CRMSalesVisitRecord

    
    def can_access_all_crm_data(self, current_user_id: UUID, session: Optional[Session] = None) -> bool:
        """
        检查用户是否有权限访问所有CRM数据
        
        判断标准（按优先级）：
        1. 是否有 crm:company:query 权限
        2. 角色是否是 COMPANY_EXECUTIVE 或 COMPANY_ADMIN
        3. UserProfile 中的 role 或 position 是否为 "admin"（兜底判断）
        
        Args:
            current_user_id: 用户ID
            session: 数据库会话（可选，如果提供则作为兜底检查 UserProfile 中的 admin 角色）
            
        Returns:
            bool: 如果有权限访问所有CRM数据返回 True，否则返回 False
        """
        # 1. 检查权限和角色（优先判断）
        try:
            roles_and_permissions = oauth_client.query_user_roles_and_permissions(user_id=current_user_id)
            permissions = roles_and_permissions.get("permissions", []) if isinstance(roles_and_permissions, dict) else []
            roles = roles_and_permissions.get("roles", []) if isinstance(roles_and_permissions, dict) else []
            
            # 1.1 检查是否有 crm:company:query 权限
            if "crm:company:query" in permissions:
                logger.info(f"User {current_user_id} has crm:company:query permission; can access all CRM data")
                return True
            
            # 1.2 检查角色是否是公司高层/公司管理员
            # roles 结构：要么是空数组 []，要么是包含字典的数组，每个字典有 code、name 等字段
            company_admin_roles = ["COMPANY_EXECUTIVE", "COMPANY_ADMIN"]
            # 从角色字典中提取 code 字段（角色代码）
            role_codes = [role.get("code") for role in roles if isinstance(role, dict) and role.get("code")]
            if any(role_code.lower() in [r.lower() for r in company_admin_roles] for role_code in role_codes):
                logger.info(f"User {current_user_id} has company admin role {role_codes}; can access all CRM data")
                return True
        except Exception as e:
            # 如果权限查询失败，记录日志但继续后续检查
            logger.warning(f"Failed to check permissions/roles for user {current_user_id}: {e}")
        
        # 2. 兜底判断：检查 UserProfile 中的 admin 角色（如果提供了 session）
        if session is not None:
            try:
                from app.repositories.user_profile import user_profile_repo
                _, role = user_profile_repo.get_crm_user_id_and_role_by_user_id(session, current_user_id)
                if role == "admin":
                    logger.info(f"User {current_user_id} is admin (from UserProfile, fallback check); can access all CRM data")
                    return True
            except Exception as e:
                # 如果查询失败，记录日志
                logger.warning(f"Failed to check UserProfile admin role for user {current_user_id}: {e}")
        
        return False
    
    def _is_admin_user(self, current_user_id: UUID, session: Session, user_permissions: Optional[List[str]] = None) -> bool:
        """
        检查当前用户是否为拜访记录的管理团队成员
        基于用户profile中的notification_tags字段判断是否包含list_visit_records权限
        或者检查是否有 report51:company:view 权限
        
        Args:
            current_user_id: 用户ID
            session: 数据库会话
            user_permissions: 可选的用户权限列表，如果提供则直接使用，避免重复查询
        """
        # 获取用户权限（如果未提供）
        if user_permissions is None:
            roles_and_permissions = oauth_client.query_user_roles_and_permissions(user_id=current_user_id)
            user_permissions = roles_and_permissions.get("permissions", [])
        
        # 1. 先检查是否有 report51:company:view 权限
        if "report51:company:view" in user_permissions:
            logger.info(f"User {current_user_id} has report51:company:view permission")
            return True
        
        # 2. 检查 notification_tags 中的 list_visit_records 权限（向后兼容）
        user_profile = user_profile_repo.get_by_user_id(session, current_user_id)
        
        if user_profile and user_profile.notification_tags:
            # 检查notification_tags中是否包含list_visit_records权限
            notification_tags = user_profile.notification_tags
            if "list_visit_records" in notification_tags:
                logger.info(f"User {current_user_id} has list_visit_records permission in notification_tags: {notification_tags}")
                return True
        
        # 如果没有找到用户档案或没有相应权限，返回False
        logger.info(f"User {current_user_id} does not have admin permissions")
        return False

    def _get_user_accessible_recorder_ids(self, session: Session, current_user_id: UUID, user_permissions: Optional[List[str]] = None) -> Optional[List[str]]:
        """
        获取当前用户可访问的记录人ID列表
        
        根据权限决定访问范围：
        - report51:company:view: 返回 None（表示可以访问所有记录，由调用方处理）
        - report51:dept:view: 返回本部门所有成员的 recorder_id
        - 都没有: 返回当前用户自己 + 所有汇报关系（递归获取所有下属） + 如果是部门负责人则包括全部门
        
        Args:
            session: 数据库会话
            current_user_id: 当前用户ID
            user_permissions: 可选的用户权限列表，如果提供则直接使用，避免重复查询
        """
        # 获取用户权限（如果未提供）
        if user_permissions is None:
            roles_and_permissions = oauth_client.query_user_roles_and_permissions(user_id=current_user_id)
            user_permissions = roles_and_permissions.get("permissions", [])
        
        # 检查是否有公司级查看权限
        if "report51:company:view" in user_permissions:
            logger.info(f"User {current_user_id} has report51:company:view permission, can access all records")
            return None  # None 表示可以访问所有记录
                
        # 获取当前用户的档案
        current_user_profile = user_profile_repo.get_by_user_id(session, current_user_id)
        
        if not current_user_profile:
            logger.warning(f"No user profile found for user_id: {current_user_id}")
            return []
        
        accessible_recorder_ids = []
        
        # 检查是否有部门级查看权限
        has_dept_view = "report51:dept:view" in user_permissions
        
        if has_dept_view and current_user_profile.department:
            # 如果有部门查看权限，返回本部门所有成员的 recorder_id
            logger.info(f"User {current_user_id} has report51:dept:view permission for department {current_user_profile.department}, getting all department members")
            department_members = user_profile_repo.get_department_members(session, current_user_profile.department)
            for member in department_members:
                if member.oauth_user_id:
                    accessible_recorder_ids.append(member.oauth_user_id)
            logger.info(f"Added {len(accessible_recorder_ids)} department members to accessible list")
        else:
            # 没有部门查看权限，使用原有逻辑：自己 + 汇报关系 + 部门负责人权限
            # 1. 添加当前用户自己（如果有recorder_id）
            if current_user_profile.oauth_user_id:
                accessible_recorder_ids.append(current_user_profile.oauth_user_id)
            
            # 2. 递归获取所有汇报关系（包括直接下属和间接下属）
            if current_user_profile.oauth_user_id:
                all_subordinates = user_profile_repo.get_all_subordinates_recursive(session, current_user_profile.oauth_user_id)
                for subordinate in all_subordinates:
                    if subordinate.oauth_user_id:
                        accessible_recorder_ids.append(subordinate.oauth_user_id)
            
            # 3. 如果是部门负责人（没有直属上级），获取全部门所有成员
            if current_user_profile.department and not current_user_profile.direct_manager_id:
                logger.info(f"User {current_user_id} is department manager for {current_user_profile.department}, getting all department members")
                department_members = user_profile_repo.get_department_members(session, current_user_profile.department)
                for member in department_members:
                    if member.oauth_user_id and member.oauth_user_id not in accessible_recorder_ids:
                        accessible_recorder_ids.append(member.oauth_user_id)
                        logger.info(f"Added department member {member.name} ({member.oauth_user_id}) to accessible list")
        
        # 去重
        accessible_recorder_ids = list(set(accessible_recorder_ids))
        logger.info(f"User {current_user_id} can access {len(accessible_recorder_ids)} recorders: {accessible_recorder_ids}")
        
        return accessible_recorder_ids

    def _get_visit_record_accessible_recorder_uuid_ids(
        self,
        session: Session,
        current_user_id: Optional[UUID],
        user_permissions: Optional[List[str]] = None,
    ) -> Optional[List[UUID]]:
        """
        统一的“拜访记录可访问范围”计算入口，避免各方法里重复写权限逻辑。

        Returns:
        - None: 可访问所有记录（管理团队/公司级权限/未提供 current_user_id 向后兼容）
        - []: 无任何可访问记录
        - [UUID, ...]: 需要按 recorder_id 过滤的 UUID 列表
        """
        if not current_user_id:
            logger.warning("No current_user_id provided, skipping access control")
            return None

        if user_permissions is None:
            roles_and_permissions = oauth_client.query_user_roles_and_permissions(user_id=current_user_id)
            user_permissions = roles_and_permissions.get("permissions", [])

        # 管理团队成员（含 report51:company:view / notification_tags:list_visit_records）可访问所有
        if self._is_admin_user(current_user_id, session, user_permissions):
            logger.info(f"Admin user {current_user_id} detected, skipping access control")
            return None

        accessible_recorder_ids = self._get_user_accessible_recorder_ids(
            session=session,
            current_user_id=current_user_id,
            user_permissions=user_permissions,
        )

        # None 表示公司级可访问所有
        if accessible_recorder_ids is None:
            return None

        # 空列表表示无权限
        if not accessible_recorder_ids:
            return []

        # 字符串ID转换为UUID（兼容 32位无短横线 UUID）
        uuid_recorder_ids: List[UUID] = []
        try:
            for rid in accessible_recorder_ids:
                if not rid:
                    continue
                if len(rid) == 32:
                    formatted_uuid = f"{rid[:8]}-{rid[8:12]}-{rid[12:16]}-{rid[16:20]}-{rid[20:32]}"
                    uuid_recorder_ids.append(UUID(formatted_uuid))
                else:
                    uuid_recorder_ids.append(UUID(rid))
        except ValueError as e:
            logger.error(f"Invalid UUID format in accessible_recorder_ids: {e}")
            return []

        return uuid_recorder_ids

    def _can_access_visit_record_by_recorder_id(
        self,
        session: Session,
        current_user_id: Optional[UUID],
        recorder_id: Optional[UUID],
    ) -> bool:
        policy = VisitRecordAccessPolicy(
            session=session,
            current_user_id=current_user_id,
            roles_and_permissions_provider=lambda user_id: oauth_client.query_user_roles_and_permissions(user_id=user_id),
            is_admin_user_fn=self._is_admin_user,
        )
        return policy.can_access_single_recorder(recorder_id)

    def query_visit_records(
        self,
        session: Session,
        request: VisitRecordQueryRequest,
        current_user_id: Optional[UUID] = None,
    ) -> Page[VisitRecordResponse]:
        """
        查询拜访记录，支持条件过滤和分页
        根据当前用户的汇报关系限制数据访问权限
        """
        # 验证分页参数
        if request.page < 1:
            request.page = 1
        if request.page_size < 1:
            request.page_size = 20
        elif request.page_size > 100:  # 限制最大页面大小为100（fastapi_pagination的限制）
            request.page_size = 100
        policy = VisitRecordAccessPolicy(
            session=session,
            current_user_id=current_user_id,
            roles_and_permissions_provider=lambda user_id: oauth_client.query_user_roles_and_permissions(user_id=user_id),
            is_admin_user_fn=self._is_admin_user,
        )
            
        # 构建基础查询：customer_level 来自跟进对象关联的 crm_accounts
        account_crm, partner_crm, customer_level_col, _customer_attribute_col, _followup_extra_col = (
            _followup_object_crm_account_join()
        )
        field_mapping = get_resolved_field_mapping(session, report_type="拜访记录查询")
        # 不在分页 SELECT 中带 extra（JSON 不可 hash，会导致 paginate unique() 失败）
        query = (
            select(
                CRMSalesVisitRecord,
                customer_level_col,
            )
            .outerjoin(account_crm, CRMSalesVisitRecord.account_id == account_crm.unique_id)
            .outerjoin(partner_crm, CRMSalesVisitRecord.partner_id == partner_crm.unique_id)
        )
        
        # 应用权限控制过滤 - 在 JOIN 之前先过滤，提高性能
        predicate = policy.list_access_predicate(CRMSalesVisitRecord)
        if predicate is not None:
            query = query.where(predicate)

        # 应用过滤条件
        if request.record_id:
            query = query.where(
                CRMSalesVisitRecord.record_id == request.record_id
            )

        if request.customer_level:
            query = query.where(customer_level_col.in_(request.customer_level))

        if request.customer_attribute:
            attr_predicate = _customer_attribute_filter_predicate(request.customer_attribute)
            if attr_predicate is None:
                return _empty_visit_record_page(request)
            query = query.where(attr_predicate)

        if request.tag_ids:
            tag_ids = list({tag_id.strip() for tag_id in request.tag_ids if tag_id and tag_id.strip()})
            if tag_ids:
                matching_account_ids = crm_account_repo.get_account_unique_ids_by_tag_ids(
                    session,
                    tag_ids,
                )
                followup_account_id = func.coalesce(
                    func.nullif(CRMSalesVisitRecord.account_id, ""),
                    CRMSalesVisitRecord.partner_id,
                )
                if matching_account_ids:
                    query = query.where(followup_account_id.in_(matching_account_ids))
                else:
                    return _empty_visit_record_page(request)

        if request.account_id:
            query = query.where(
                CRMSalesVisitRecord.account_id.in_(request.account_id)
            )

        if request.account_name:
            # 客户名称完全匹配，支持多选
            # 性能优化：使用精确匹配（可以使用索引），比模糊匹配性能更好
            query = query.where(
                CRMSalesVisitRecord.account_name.in_(request.account_name)
            )

        if request.partner_id:
            query = query.where(
                CRMSalesVisitRecord.partner_id.in_(request.partner_id)
            )
        if request.partner_name:
            # 合作伙伴名称完全匹配，支持多选
            # 性能优化：使用精确匹配（可以使用索引），比模糊匹配性能更好
            query = query.where(
                CRMSalesVisitRecord.partner_name.in_(request.partner_name)
            )

        if request.opportunity_id:
            query = query.where(
                CRMSalesVisitRecord.opportunity_id.in_(request.opportunity_id)
            )

        if request.opportunity_name:
            query = query.where(
                CRMSalesVisitRecord.opportunity_name.in_(request.opportunity_name)
            )

        if request.visit_communication_date_start:
            try:
                start_date = datetime.strptime(request.visit_communication_date_start, "%Y-%m-%d").date()
                query = query.where(CRMSalesVisitRecord.visit_communication_date >= start_date)
            except ValueError:
                pass  # 忽略无效日期格式

        if request.visit_communication_date_end:
            try:
                end_date = datetime.strptime(request.visit_communication_date_end, "%Y-%m-%d").date()
                query = query.where(CRMSalesVisitRecord.visit_communication_date <= end_date)
            except ValueError:
                pass  # 忽略无效日期格式

        if request.recorder:
            query = query.where(
                CRMSalesVisitRecord.recorder.in_(request.recorder)
            )

        if request.department:
            query = query.where(
                CRMSalesVisitRecord.recorder_department_name.in_(request.department)
            )

        if request.visit_communication_method:
            query = query.where(
                CRMSalesVisitRecord.visit_communication_method.in_(request.visit_communication_method)
            )

        if request.visit_purpose:
            query = query.where(
                CRMSalesVisitRecord.visit_purpose.in_(request.visit_purpose)
            )

        if request.followup_quality_level:
            query = query.where(
                or_(
                    CRMSalesVisitRecord.followup_quality_level_zh.in_(request.followup_quality_level),
                    CRMSalesVisitRecord.followup_quality_level_en.in_(request.followup_quality_level)
                )
            )

        if request.next_steps_quality_level:
            query = query.where(
                or_(
                    CRMSalesVisitRecord.next_steps_quality_level_zh.in_(request.next_steps_quality_level),
                    CRMSalesVisitRecord.next_steps_quality_level_en.in_(request.next_steps_quality_level)
                )
            )

        if request.assessment_flag:
            query = query.where(
                CRMSalesVisitRecord.assessment_flag.in_(request.assessment_flag)
            )

        if request.visit_type:
            query = query.where(
                CRMSalesVisitRecord.visit_type.in_(request.visit_type)
            )

        if request.subject:
            query = query.where(
                CRMSalesVisitRecord.subject.in_(request.subject)
            )

        if request.record_type:
            query = query.where(
                CRMSalesVisitRecord.record_type.in_(request.record_type)
            )

        if request.is_first_visit is not None:
            query = query.where(
                CRMSalesVisitRecord.is_first_visit == request.is_first_visit
            )

        if request.is_call_high is not None:
            query = query.where(
                CRMSalesVisitRecord.is_call_high == request.is_call_high
            )

        # 处理创建时间筛选 - 将北京时间的日期转换为UTC时间范围
        if request.last_modified_time_start:
            utc_start_datetime = convert_beijing_date_to_utc_range(request.last_modified_time_start, is_start=True)
            if utc_start_datetime:
                query = query.where(CRMSalesVisitRecord.last_modified_time >= utc_start_datetime)

        if request.last_modified_time_end:
            utc_end_datetime = convert_beijing_date_to_utc_range(request.last_modified_time_end, is_start=False)
            if utc_end_datetime:
                query = query.where(CRMSalesVisitRecord.last_modified_time <= utc_end_datetime)

        # 应用排序 - 默认按拜访日期降序
        sort_field = getattr(CRMSalesVisitRecord, request.sort_by, CRMSalesVisitRecord.visit_communication_date)
        if request.sort_direction.lower() == "desc":
            query = query.order_by(desc(sort_field))
        else:
            query = query.order_by(asc(sort_field))

        # 执行分页查询
        params = Params(page=request.page, size=request.page_size)
        result = paginate(session, query, params)

        # 批量加载跟进对象 extra（用于 tags），避免 SELECT JSON 导致分页去重失败
        followup_account_ids = list({
            fid
            for record, _ in result.items
            if (fid := _resolve_followup_object_id(record.account_id, record.partner_id))
        })
        followup_extra_by_account_id: Dict[str, Any] = {}
        if followup_account_ids:
            for account in crm_account_repo.get_by_account_ids(session, followup_account_ids):
                if account.unique_id and account.extra is not None:
                    followup_extra_by_account_id[account.unique_id] = account.extra

        # 优化：批量获取部门信息，避免N+1查询
        # 收集所有需要查询的recorder_id（UUID格式）
        recorder_ids = set()
        for record, _customer_level in result.items:
            if record.recorder_id:
                recorder_ids.add(record.recorder_id)
        
        # 批量查询部门信息 - 优先通过 user_department_relation + department_mirror（避免依赖 profiles）
        department_map = {}
        if recorder_ids:
            recorder_id_strs = [str(rid) for rid in recorder_ids]
            # 1) recorder(user_id) -> department_id
            user_dept_map = user_department_relation_repo.get_primary_department_by_user_ids(
                session,
                recorder_id_strs,
            )

            # 2) department_id -> department_name
            dept_ids = [d for d in (user_dept_map or {}).values() if d]
            dept_name_map = department_mirror_repo.get_department_names_by_ids(session, dept_ids)

            # 3) recorder_id(UUID) -> department_name
            for user_id_str, dept_id in (user_dept_map or {}).items():
                if not user_id_str or not dept_id:
                    continue
                try:
                    recorder_uuid = UUID(user_id_str)
                except ValueError:
                    continue
                name = dept_name_map.get(dept_id)
                if name:
                    department_map[recorder_uuid] = name

        # 转换结果格式 - 复用现有模型
        items = []
        for record, customer_level in result.items:
            department = department_map.get(record.recorder_id) if record.recorder_id else None
            followup_id = _resolve_followup_object_id(record.account_id, record.partner_id)
            raw_extra = followup_extra_by_account_id.get(followup_id) if followup_id else None
            followup_extra = raw_extra if isinstance(raw_extra, dict) else None
            customer_attribute = resolve_customer_attribute_display_label(
                record.account_id,
                record.partner_id,
                field_mapping,
            )
            items.append(
                _convert_to_response(
                    record,
                    customer_level,
                    customer_attribute,
                    department,
                    followup_extra=followup_extra,
                )
            )

        # 返回自定义分页结果
        return Page(
            items=items,
            total=result.total,
            page=result.page,
            size=result.size,
            pages=result.pages
        )

    def get_visit_record_filter_option_values(
        self,
        session: Session,
        form_type: str,
    ) -> Dict[str, List[str]]:
        """一次扫描 crm_sales_visit_records，聚合去重值（响应 key 与原 filter-options 一致）。"""
        field_specs = list(_VISIT_RECORD_BASE_FILTER_FIELDS)
        if form_type == "simple":
            field_specs.extend(_VISIT_RECORD_SIMPLE_FILTER_FIELDS)
        else:
            field_specs.extend(_VISIT_RECORD_COMPLETE_FILTER_FIELDS)

        select_parts = [
            (
                f"GROUP_CONCAT(DISTINCT `{column}` ORDER BY `{column}` "
                f"SEPARATOR :sep) AS `{column}`"
            )
            for _response_key, column in field_specs
        ]
        sql = f"SELECT {', '.join(select_parts)} FROM crm_sales_visit_records"

        session.exec(text("SET SESSION group_concat_max_len = 1048576"))
        row = session.exec(text(sql), params={"sep": _VISIT_RECORD_FILTER_OPTION_SEP}).one()
        if row is None:
            options = {response_key: [] for response_key, _ in field_specs}
        else:
            options = {
                response_key: _split_group_concat(row[i])
                for i, (response_key, _) in enumerate(field_specs)
            }
        options.update(_VISIT_RECORD_QUALITY_FILTER_OPTIONS)
        return options

    def get_visit_record_by_id(
        self,
        session: Session,
        record_id: str,
        current_user_id: Optional[UUID] = None,
    ) -> Optional[VisitRecordResponse]:
        """
        根据ID获取单个拜访记录
        根据当前用户的汇报关系限制数据访问权限
        """
        # 单条记录查询：先拿到记录本身，再做权限判断，避免 IN 大集合
        account_crm, partner_crm, customer_level_col, _customer_attribute_col, followup_extra_col = (
            _followup_object_crm_account_join()
        )
        field_mapping = get_resolved_field_mapping(session, report_type="拜访记录详情")
        result = session.exec(
            select(
                CRMSalesVisitRecord,
                customer_level_col,
                followup_extra_col,
            )
            .outerjoin(account_crm, CRMSalesVisitRecord.account_id == account_crm.unique_id)
            .outerjoin(partner_crm, CRMSalesVisitRecord.partner_id == partner_crm.unique_id)
            .where(CRMSalesVisitRecord.record_id == record_id)
        ).first()

        if not result:
            return None

        record, customer_level, followup_extra = result
        customer_attribute = resolve_customer_attribute_display_label(
            record.account_id,
            record.partner_id,
            field_mapping,
        )

        if not self._can_access_visit_record_by_recorder_id(
            session=session,
            current_user_id=current_user_id,
            recorder_id=getattr(record, "recorder_id", None),
        ):
            return None

        # department 仅用于展示，不再作为权限判断依据
        department: Optional[str] = None
        if getattr(record, "recorder_id", None):
            recorder_user_id = str(record.recorder_id)
            dept_id = user_department_relation_repo.get_primary_department_by_user_ids(
                session,
                [recorder_user_id],
            ).get(recorder_user_id)
            if dept_id:
                department = department_mirror_repo.get_department_name_by_id(session, dept_id)

        return _convert_to_response(
            record,
            customer_level,
            customer_attribute,
            department,
            followup_extra=followup_extra if isinstance(followup_extra, dict) else None,
        )

    def update_visit_record_comments(
        self,
        session: Session,
        record_id: str,
        comments: Optional[List[Dict[str, Any]]],
        current_user_id: Optional[UUID] = None
    ) -> Optional[VisitRecordResponse]:
        """
        更新指定拜访记录的 comments 字段（JSON数组）
        调用方仅传入本次新增的评论；在既有数据后追加落库。
        安全保护：不得修改/删除他人评论。HTTP 接口应在入口处校验 payload 的 author_id；
        此处仍忽略 author_id 非当前用户的条目，以防绕过 API 直接调用本方法。
        """
        # 更新评论只需要查询拜访记录主表即可
        query = select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)

        record = session.exec(query).first()
        if not record:
            return None

        # 单条记录权限判断：避免生成越来越大的 IN 列表
        if not self._can_access_visit_record_by_recorder_id(
            session=session,
            current_user_id=current_user_id,
            recorder_id=getattr(record, "recorder_id", None),
        ):
            return None

        # 安全保护：不得改动他人评论；payload 只追加当前用户的新评论
        current_user_id_str = str(current_user_id or "")
        now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))

        existing_raw = record.comments if isinstance(record.comments, list) else []
        kept_others: List[Dict[str, Any]] = []
        existing_my: List[Dict[str, Any]] = []
        for item in existing_raw:
            if not isinstance(item, dict):
                continue
            if str(item.get("author_id") or "") != current_user_id_str:
                kept_others.append(item)
            else:
                existing_my.append(item)

        # 仅采纳 payload 中 author_id=当前用户 的评论并追加；created_at 为空则用北京时间补齐
        appended: List[Dict[str, Any]] = []
        for c in (comments or []):
            if not isinstance(c, dict):
                continue
            if str(c.get("author_id") or "") != current_user_id_str:
                continue
            created_at = c.get("created_at") or now_bj
            if isinstance(created_at, datetime):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = str(created_at)
            appended.append(
                {
                    "author_id": current_user_id_str,
                    "author": c.get("author"),
                    "content": c.get("content"),
                    "type": c.get("type"),
                    "created_at": created_at_str,
                }
            )

        merged: List[Dict[str, Any]] = kept_others + existing_my + appended

        def _sort_key(x: Dict[str, Any]) -> tuple[int, str]:
            v = str(x.get("created_at") or "")
            try:
                return (0, datetime.fromisoformat(v).isoformat())
            except Exception:
                return (1, v)

        merged.sort(key=_sort_key)
        record.comments = merged
        session.add(record)
        session.commit()
        session.refresh(record)

        # 返回完整记录（便于上层推送消息等后续处理）
        return _convert_to_response(record, None, None, None)


# 创建repository实例
visit_record_repo = VisitRecordRepo()
