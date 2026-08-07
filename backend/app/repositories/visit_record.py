from typing import Any, Dict, Optional, List
from datetime import date, datetime
import logging
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import aliased
from sqlmodel import Session, select, func, desc, asc
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import paginate
from uuid import UUID
from zoneinfo import ZoneInfo

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_system_configurations import CRMSystemConfiguration
from app.models.crm_accounts import CRMAccount
from app.api.routes.crm.models import (
    VisitAttachment,
    VisitRecordQueryRequest,
    VisitRecordResponse,
    VisitRecordRowPermissions,
)
from app.core.config import settings
from app.repositories.crm_account import crm_account_repo
from app.repositories.base_repo import BaseRepo
from app.repositories.user_profile import user_profile_repo
from app.repositories.user_department_relation import user_department_relation_repo
from app.repositories.department_mirror import department_mirror_repo
from app.services.crm_config_service import get_resolved_field_mapping
from app.utils.crm_account_tags import parse_account_tags, resolve_followup_account_id
from app.permissions.follow_up_permission_service import follow_up_permission_service
from app.utils.crm_comments import (
    CRMCommentValidationError,
    has_nonempty_comments,
    merge_append_crm_comments,
)
from app.utils.crm_followup_object import (
    FOLLOWUP_OBJECT_TYPE_END_CUSTOMER,
    FOLLOWUP_OBJECT_TYPE_LEAD,
    FOLLOWUP_OBJECT_TYPE_PARTNER,
    FOLLOWUP_OBJECT_TYPES,
    apply_followup_object_to_response_dict,
    resolve_crm_account_join_id,
    resolve_followup_object_from_record,
)
from app.utils.crm_followup_object_type import resolve_customer_attribute_display_label_for_object
from app.repositories.visit_record_revisions import visit_record_revisions_repo
from app.utils.date_utils import (
    convert_beijing_date_to_utc_range,
    get_visit_record_revise_entry_denial_reason,
    utc_datetime_to_beijing_date,
)

logger = logging.getLogger(__name__)


VisitRecordCommentError = CRMCommentValidationError


class VisitRecordRevisionError(ValueError):
    """拜访记录修订时的业务错误。"""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


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
# followup_object_types 在路由层与 customer_attributes 同源下发，不在此聚合
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


def _visit_record_followup_object_type_is_set(object_type: str):
    trimmed_type = func.trim(CRMSalesVisitRecord.followup_object_type)
    trimmed_id = func.trim(CRMSalesVisitRecord.followup_object_id)
    return and_(
        CRMSalesVisitRecord.followup_object_type.isnot(None),
        trimmed_type == object_type,
        CRMSalesVisitRecord.followup_object_id.isnot(None),
        trimmed_id != "",
    )


def _visit_record_followup_object_is_empty():
    trimmed_type = func.trim(CRMSalesVisitRecord.followup_object_type)
    trimmed_id = func.trim(CRMSalesVisitRecord.followup_object_id)
    return or_(
        CRMSalesVisitRecord.followup_object_type.is_(None),
        and_(trimmed_type == "", trimmed_id == ""),
    )


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


def _visit_record_partner_id_is_empty():
    trimmed = func.trim(CRMSalesVisitRecord.partner_id)
    return or_(
        CRMSalesVisitRecord.partner_id.is_(None),
        trimmed == "",
    )


def _visit_record_followup_is_partner_type():
    """
    跟进对象为合作伙伴：
    - 新写入：followup_object_type=partner（可同时双写 partner_*）
    - 历史：followup_object_* 为空，且 account 空、partner 非空
    历史数据可能 account/partner 同时有值（跟进对象实为 account），不可仅凭 partner 有值命中。
    """
    return or_(
        _visit_record_followup_object_type_is_set(FOLLOWUP_OBJECT_TYPE_PARTNER),
        and_(
            _visit_record_account_id_is_empty(),
            _visit_record_followup_object_is_empty(),
            _visit_record_partner_id_is_set(),
        ),
    )


def _visit_record_followup_is_end_customer_type():
    """
    跟进对象为最终客户：
    - 新写入：followup_object_type=end_customer（可同时双写 account_*）
    - 历史/双写：account_id 非空，且不是 lead/partner 跟进对象
    """
    return or_(
        _visit_record_followup_object_type_is_set(FOLLOWUP_OBJECT_TYPE_END_CUSTOMER),
        and_(
            _visit_record_account_id_is_set(),
            ~_visit_record_followup_object_type_is_set(FOLLOWUP_OBJECT_TYPE_LEAD),
            ~_visit_record_followup_object_type_is_set(FOLLOWUP_OBJECT_TYPE_PARTNER),
        ),
    )


def _visit_record_followup_is_lead_type():
    """跟进对象为线索：followup_object_type=lead。"""
    return _visit_record_followup_object_type_is_set(FOLLOWUP_OBJECT_TYPE_LEAD)


def _customer_attribute_filter_predicate(selected_values: List[str]):
    """
    customer_attribute 接受 end_customer / partner / lead 键（与 filter-options 一致），多选 OR。
    - end_customer：followup_object_type=end_customer，或 account_id 非空（排除 lead/partner）
    - partner：followup_object_type=partner，或历史仅 partner_* 有值
    - lead：followup_object_type=lead
    """
    selected = {value.strip() for value in selected_values if value and value.strip()}
    if not selected:
        return None

    predicates = []
    if "end_customer" in selected:
        predicates.append(_visit_record_followup_is_end_customer_type())
    if "lead" in selected:
        predicates.append(_visit_record_followup_is_lead_type())
    if "partner" in selected:
        predicates.append(_visit_record_followup_is_partner_type())
    if not predicates:
        return None
    return or_(*predicates) if len(predicates) > 1 else predicates[0]


def _merge_string_filter_values(*sources: Optional[List[str]]) -> list[str]:
    """多组字符串筛选值合并去重（保持首次出现顺序）。"""
    merged: list[str] = []
    seen: set[str] = set()
    for values in sources:
        if not values:
            continue
        for value in values:
            key = (value or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(key)
    return merged


def _merge_followup_object_type_filter_values(
    customer_attribute: Optional[List[str]],
    followup_object_type: Optional[List[str]],
) -> list[str]:
    """customer_attribute 与 followup_object_type 视为同一维度，合并去重。"""
    return _merge_string_filter_values(customer_attribute, followup_object_type)


def _followup_object_type_filter_predicate(selected_values: List[str]):
    """
    合并后的跟进对象类型筛选：
    - 已知键走 customer_attribute 推断（含历史兼容）
    - 其余未知键仍按 followup_object_type 列精确匹配
    """
    if not selected_values:
        return None

    known = [value for value in selected_values if value in FOLLOWUP_OBJECT_TYPES]
    unknown = [value for value in selected_values if value not in FOLLOWUP_OBJECT_TYPES]

    predicates = []
    if known:
        known_predicate = _customer_attribute_filter_predicate(known)
        if known_predicate is not None:
            predicates.append(known_predicate)
    if unknown:
        predicates.append(CRMSalesVisitRecord.followup_object_type.in_(unknown))
    if not predicates:
        return None
    return or_(*predicates) if len(predicates) > 1 else predicates[0]


def _followup_object_id_filter_predicate(selected_ids: List[str]):
    """
    跟进对象 ID 筛选：followup_object_id / account_id / partner_id 任一命中即可（含历史）。
    """
    if not selected_ids:
        return None
    return or_(
        CRMSalesVisitRecord.followup_object_id.in_(selected_ids),
        CRMSalesVisitRecord.account_id.in_(selected_ids),
        CRMSalesVisitRecord.partner_id.in_(selected_ids),
    )


def _followup_object_name_filter_predicate(selected_names: List[str]):
    """
    跟进对象名称筛选：followup_object_name / account_name / partner_name 任一命中即可（含历史）。
    """
    if not selected_names:
        return None
    return or_(
        CRMSalesVisitRecord.followup_object_name.in_(selected_names),
        CRMSalesVisitRecord.account_name.in_(selected_names),
        CRMSalesVisitRecord.partner_name.in_(selected_names),
    )


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
    if attachment is None or attachment == "":
        # 库内可能存空串；VisitRecordResponse.attachment 只接受 None / VisitAttachment
        record_dict["attachment"] = None
    else:
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

    apply_followup_object_to_response_dict(record_dict)
    response = VisitRecordResponse.model_validate(record_dict)
    
    # 添加关联字段
    response.customer_level = customer_level
    response.customer_attribute = customer_attribute
    response.tags = [
        AccountTagOptionOut(id=tag.id, name=tag.name)
        for tag in parse_account_tags(followup_extra if isinstance(followup_extra, dict) else None)
    ]
    response.department = department  # 拜访人部门
    response.has_comments = has_nonempty_comments(record.comments)

    return response


def _visit_record_has_comments_predicate():
    """构建 SQL 谓词：comments 数组是否非空（不区分 comment / task）。"""
    comments_col = CRMSalesVisitRecord.comments
    return and_(
        comments_col.isnot(None),
        func.json_length(comments_col) > 0,
    )


class VisitRecordRepo(BaseRepo):
    model_cls = CRMSalesVisitRecord

    def _can_view_visit_record(
        self,
        session: Session,
        current_user_id: Optional[UUID],
        record: CRMSalesVisitRecord,
    ) -> bool:
        if not current_user_id:
            return True
        return follow_up_permission_service.check_view(session, current_user_id, record)

    def _can_edit_visit_record(
        self,
        session: Session,
        current_user_id: Optional[UUID],
        record: CRMSalesVisitRecord,
    ) -> bool:
        if not current_user_id:
            return False
        return follow_up_permission_service.check_edit(session, current_user_id, record)

    def _apply_visit_record_list_permission(
        self,
        session: Session,
        query,
        *,
        current_user_id: Optional[UUID],
    ):
        """列表权限过滤：OAuth ``follow_up`` data-scope → SQL WHERE（Wave A3：已移除 VisitRecordAccessPolicy）。"""
        if not current_user_id:
            logger.warning("No current_user_id provided, skipping visit record list permission filter")
            return query

        perm_where = follow_up_permission_service.list_perm_where(session, current_user_id)
        return query.where(perm_where)

    def _resolve_row_permissions_for_page(
        self,
        session: Session,
        *,
        current_user_id: Optional[UUID],
        records: list[CRMSalesVisitRecord],
    ) -> dict[str, VisitRecordRowPermissions]:
        if not current_user_id or not records:
            return {}

        raw = follow_up_permission_service.batch_row_permissions(session, current_user_id, records)
        return {
            record_id: VisitRecordRowPermissions(**perms)
            for record_id, perms in raw.items()
        }

    def _load_allowed_communication_methods(self, session: Session) -> set[str]:
        stmt = select(CRMSystemConfiguration.config_key).where(
            CRMSystemConfiguration.config_type == "CommunicationMediumCategory",
            CRMSystemConfiguration.is_active == True,
        )
        return {str(x).strip() for x in session.exec(stmt).all() if str(x).strip()}

    def _format_field_value_for_revision(self, field_name: str, value: Any) -> Optional[str]:
        if value is None:
            return None
        if field_name == "visit_communication_date":
            if isinstance(value, date):
                return value.isoformat()
            return str(value).strip() or None
        return str(value).strip() or None

    def supervised_revise_visit_record(
        self,
        session: Session,
        *,
        record_id: str,
        current_user_id: UUID,
        revised_by_name: Optional[str],
        visit_communication_date: Optional[str] = None,
        visit_communication_method: Optional[str] = None,
        followup_record: Optional[str] = None,
        next_steps: Optional[str] = None,
    ):
        """
        修改拜访记录：需 sales:follow_up:edit 且在可查看范围内；
        允许改跟进日期、跟进方式、跟进记录、下一步计划；
        录入自然日窗口见 CRM_VISIT_RECORD_REVISE_ENTRY_WINDOW_DAYS，
        每日截止时间见 CRM_VISIT_RECORD_REVISE_DAILY_CUTOFF_TIME。
        返回 (VisitRecordResponse, CRMSalesVisitRecordRevision)。
        """
        from app.core.config import settings

        if (
            visit_communication_date is None
            and visit_communication_method is None
            and followup_record is None
            and next_steps is None
        ):
            raise VisitRecordRevisionError("请至少提供一个待修改字段")

        record = session.exec(
            select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
        ).first()
        if not record:
            raise VisitRecordRevisionError("跟进记录不存在", "not_found")

        recorder_id = getattr(record, "recorder_id", None)
        if not self._can_edit_visit_record(session, current_user_id, record):
            raise VisitRecordRevisionError("无权限修改该跟进记录", "forbidden")

        created_beijing_date = utc_datetime_to_beijing_date(record.last_modified_time)
        denial = get_visit_record_revise_entry_denial_reason(created_beijing_date)
        if denial:
            raise VisitRecordRevisionError(denial)

        allowed_methods = self._load_allowed_communication_methods(session)
        changes: list[dict[str, Any]] = []

        if visit_communication_date is not None:
            raw_date = str(visit_communication_date).strip()
            if not raw_date:
                raise VisitRecordRevisionError("跟进日期不能为空")
            try:
                new_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise VisitRecordRevisionError("跟进日期格式无效，应为 YYYY-MM-DD") from exc
            old_date_str = self._format_field_value_for_revision(
                "visit_communication_date", record.visit_communication_date
            )
            new_date_str = new_date.isoformat()
            if old_date_str != new_date_str:
                changes.append(
                    {
                        "field": "visit_communication_date",
                        "old": old_date_str,
                        "new": new_date_str,
                    }
                )
                record.visit_communication_date = new_date

        if visit_communication_method is not None:
            new_method = str(visit_communication_method).strip() or None
            if new_method and allowed_methods and new_method not in allowed_methods:
                raise VisitRecordRevisionError("跟进方式不在系统配置允许范围内")
            old_method = self._format_field_value_for_revision(
                "visit_communication_method", record.visit_communication_method
            )
            if old_method != new_method:
                changes.append(
                    {
                        "field": "visit_communication_method",
                        "old": old_method,
                        "new": new_method,
                    }
                )
                record.visit_communication_method = new_method

        if followup_record is not None:
            new_followup = str(followup_record).strip() or None
            old_followup = self._format_field_value_for_revision(
                "followup_record", record.followup_record
            )
            if old_followup != new_followup:
                changes.append(
                    {
                        "field": "followup_record",
                        "old": old_followup,
                        "new": new_followup,
                    }
                )
                record.followup_record = new_followup
                # 与创建链路一致（默认关双语）：zh/en 均写原文，避免列表/导出读到旧值
                record.followup_record_zh = new_followup
                record.followup_record_en = new_followup

        if next_steps is not None:
            new_next_steps = str(next_steps).strip() or None
            old_next_steps = self._format_field_value_for_revision(
                "next_steps", record.next_steps
            )
            if old_next_steps != new_next_steps:
                changes.append(
                    {
                        "field": "next_steps",
                        "old": old_next_steps,
                        "new": new_next_steps,
                    }
                )
                record.next_steps = new_next_steps
                # 同上：与创建链路一致，zh/en 均写原文
                record.next_steps_zh = new_next_steps
                record.next_steps_en = new_next_steps

        if not changes:
            raise VisitRecordRevisionError("提交内容与当前记录一致，无需修改")

        revision_seq = int(getattr(record, "revision_count", 0) or 0) + 1
        record.revision_count = revision_seq

        message_type = settings.ALDEBARAN_VISIT_RECORD_REVISED_MESSAGE_TYPE
        dedupe_key = f"{message_type}:{record_id}:rev:{revision_seq}"
        revised_by_id = str(current_user_id)

        revision = visit_record_revisions_repo.create(
            session,
            record_id=record_id,
            revision_seq=revision_seq,
            revised_by_id=revised_by_id,
            revised_by_name=(revised_by_name or "").strip() or None,
            changes=changes,
            aldebaran_message_type=message_type,
            aldebaran_dedupe_key=dedupe_key,
            card_push_status="pending",
        )

        session.add(record)
        session.commit()
        session.refresh(record)
        session.refresh(revision)

        response = self.get_visit_record_by_id(
            session=session,
            record_id=record_id,
            current_user_id=current_user_id,
        )
        if response is None:
            raise VisitRecordRevisionError("跟进记录不存在", "not_found")
        return response, revision

    def list_visit_record_revisions(
        self,
        session: Session,
        record_id: str,
        current_user_id: Optional[UUID] = None,
    ):
        record = session.exec(
            select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
        ).first()
        if not record:
            return None
        if not self._can_view_visit_record(
            session=session,
            current_user_id=current_user_id,
            record=record,
        ):
            return None
        return visit_record_revisions_repo.list_by_record_id(session, record_id)

    def query_visit_records(
        self,
        session: Session,
        request: VisitRecordQueryRequest,
        current_user_id: Optional[UUID] = None,
        *,
        include_row_permissions: bool = False,
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
        
        query = self._apply_visit_record_list_permission(
            session,
            query,
            current_user_id=current_user_id,
        )

        # 应用过滤条件
        if request.record_id:
            query = query.where(
                CRMSalesVisitRecord.record_id == request.record_id
            )

        if request.customer_level:
            query = query.where(customer_level_col.in_(request.customer_level))

        merged_followup_types = _merge_followup_object_type_filter_values(
            request.customer_attribute,
            request.followup_object_type,
        )
        if merged_followup_types:
            attr_predicate = _followup_object_type_filter_predicate(merged_followup_types)
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

        merged_followup_ids = _merge_string_filter_values(
            request.followup_object_id,
            request.account_id,
            request.partner_id,
        )
        if merged_followup_ids:
            id_predicate = _followup_object_id_filter_predicate(merged_followup_ids)
            if id_predicate is not None:
                query = query.where(id_predicate)

        merged_followup_names = _merge_string_filter_values(
            request.followup_object_name,
            request.account_name,
            request.partner_name,
        )
        if merged_followup_names:
            name_predicate = _followup_object_name_filter_predicate(merged_followup_names)
            if name_predicate is not None:
                query = query.where(name_predicate)

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

        if request.has_comments is not None:
            has_comments_predicate = _visit_record_has_comments_predicate()
            if request.has_comments:
                query = query.where(has_comments_predicate)
            else:
                query = query.where(~has_comments_predicate)

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
            if (fid := resolve_followup_account_id(record.account_id, record.partner_id))
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
        page_records = [record for record, _ in result.items]
        row_permissions_map = (
            self._resolve_row_permissions_for_page(
                session,
                current_user_id=current_user_id,
                records=page_records,
            )
            if include_row_permissions
            else {}
        )
        items = []
        for record, customer_level in result.items:
            department = department_map.get(record.recorder_id) if record.recorder_id else None
            crm_account_join_id = resolve_crm_account_join_id(
                followup_object_type=record.followup_object_type,
                followup_object_id=record.followup_object_id,
                account_id=record.account_id,
                partner_id=record.partner_id,
            )
            raw_extra = followup_extra_by_account_id.get(crm_account_join_id) if crm_account_join_id else None
            followup_extra = raw_extra if isinstance(raw_extra, dict) else None
            followup_obj = resolve_followup_object_from_record(record)
            customer_attribute = resolve_customer_attribute_display_label_for_object(
                followup_obj,
                field_mapping,
            )
            response = _convert_to_response(
                record,
                customer_level,
                customer_attribute,
                department,
                followup_extra=followup_extra,
            )
            record_id = str(getattr(record, "record_id", "") or "").strip()
            if record_id and record_id in row_permissions_map:
                response.permissions = row_permissions_map[record_id]
            items.append(response)

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
        followup_obj = resolve_followup_object_from_record(record)
        customer_attribute = resolve_customer_attribute_display_label_for_object(
            followup_obj,
            field_mapping,
        )

        if not self._can_view_visit_record(
            session=session,
            current_user_id=current_user_id,
            record=record,
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
        if not self._can_view_visit_record(
            session=session,
            current_user_id=current_user_id,
            record=record,
        ):
            return None

        current_user_id_str = str(current_user_id or "")
        merged, _appended = merge_append_crm_comments(
            record.comments,
            comments,
            current_user_id_str,
            now=datetime.now(ZoneInfo("Asia/Shanghai")),
        )
        record.comments = merged
        session.add(record)
        session.commit()
        session.refresh(record)

        # 返回完整记录（便于上层推送消息等后续处理）
        return _convert_to_response(record, None, None, None)


# 创建repository实例
visit_record_repo = VisitRecordRepo()
