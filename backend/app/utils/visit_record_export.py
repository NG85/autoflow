"""CRM 跟进记录导出列定义与行构建。"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, NamedTuple, Optional, Sequence

from app.api.routes.crm.models import RecordType
from app.utils.crm_comments import format_crm_comments_for_export


class VisitRecordExportColumn(NamedTuple):
    key: str
    header_zh: str
    header_en: str


VISIT_RECORD_EXPORT_COLUMNS: tuple[VisitRecordExportColumn, ...] = (
    VisitRecordExportColumn("record_id", "ID", "ID"),
    VisitRecordExportColumn("customer_level", "客户分类", "Customer Level"),
    VisitRecordExportColumn("followup_object_name", "跟进对象", "Follow-up Object"),
    VisitRecordExportColumn("followup_object_id", "跟进对象ID", "Follow-up Object ID"),
    VisitRecordExportColumn("customer_attribute", "跟进对象属性", "Follow-up Object Attribute"),
    VisitRecordExportColumn("is_first_visit", "是否首次拜访", "First Visit"),
    VisitRecordExportColumn("is_call_high", "是否Call High", "Call High"),
    VisitRecordExportColumn(
        "external_collaboration_partner_name",
        "外部协同合作伙伴",
        "External Collaboration Partner",
    ),
    VisitRecordExportColumn(
        "external_collaboration_partner_id",
        "外部协同合作伙伴ID",
        "External Collaboration Partner ID",
    ),
    VisitRecordExportColumn("opportunity_name", "商机名称", "Opportunity Name"),
    VisitRecordExportColumn("opportunity_number", "商机编号", "Opportunity Number"),
    VisitRecordExportColumn("opportunity_id", "商机ID", "Opportunity ID"),
    VisitRecordExportColumn("visit_communication_date", "跟进日期", "Follow-up Date"),
    VisitRecordExportColumn("recorder", "负责销售", "Person in Charge"),
    VisitRecordExportColumn("department", "所在团队", "Department"),
    VisitRecordExportColumn("contact_position", "联系人职位", "Contact Position"),
    VisitRecordExportColumn("contact_name", "联系人姓名", "Contact Name"),
    VisitRecordExportColumn(
        "collaborative_participants",
        "协同参与人",
        "Collaborative Participants",
    ),
    VisitRecordExportColumn("visit_communication_method", "跟进方式", "Follow-up Method"),
    VisitRecordExportColumn("visit_purpose", "拜访目的", "Visit Purpose"),
    VisitRecordExportColumn("attachment_location", "附件地点", "Attachment Location"),
    VisitRecordExportColumn("attachment_latitude", "附件纬度", "Attachment Latitude"),
    VisitRecordExportColumn("attachment_longitude", "附件经度", "Attachment Longitude"),
    VisitRecordExportColumn("attachment_taken_at", "附件拍摄时间", "Attachment Taken At"),
    VisitRecordExportColumn("followup_record", "跟进记录", "Follow-up Record"),
    VisitRecordExportColumn(
        "followup_quality_level",
        "AI对跟进记录质量评估",
        "AI Follow-up Record Quality Evaluation",
    ),
    VisitRecordExportColumn(
        "followup_quality_reason",
        "AI对跟进记录质量评估详情",
        "AI Follow-up Record Quality Evaluation Details",
    ),
    VisitRecordExportColumn("next_steps", "下一步计划", "Next Steps"),
    VisitRecordExportColumn(
        "next_steps_quality_level",
        "AI对下一步计划质量评估",
        "AI Next Steps Quality Evaluation",
    ),
    VisitRecordExportColumn(
        "next_steps_quality_reason",
        "AI对下一步计划质量评估详情",
        "AI Next Steps Quality Evaluation Details",
    ),
    VisitRecordExportColumn("assessment_flag", "评估标记", "Assessment Flag"),
    VisitRecordExportColumn("record_type", "记录类型", "Record Type"),
    VisitRecordExportColumn("visit_type", "信息来源", "Information Source"),
    VisitRecordExportColumn("remarks", "备注", "Remarks"),
    VisitRecordExportColumn("comments", "评论", "Comments"),
    VisitRecordExportColumn("tasks", "任务", "Tasks"),
    VisitRecordExportColumn("last_modified_time", "创建时间", "Created Time"),
)

VISIT_RECORD_EXPORT_COLUMN_KEYS: tuple[str, ...] = tuple(
    column.key for column in VISIT_RECORD_EXPORT_COLUMNS
)
_EXPORT_COLUMN_BY_KEY: dict[str, VisitRecordExportColumn] = {
    column.key: column for column in VISIT_RECORD_EXPORT_COLUMNS
}


class UnknownVisitRecordExportColumn(ValueError):
    """请求了未知的导出列。"""


def resolve_export_columns(requested: Optional[Sequence[str]]) -> list[str]:
    """解析导出列：未传或空则全量；否则按传入顺序去重。未知列抛错。"""
    if not requested:
        return list(VISIT_RECORD_EXPORT_COLUMN_KEYS)

    selected: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for raw in requested:
        key = (raw or "").strip()
        if not key or key in seen:
            continue
        if key not in _EXPORT_COLUMN_BY_KEY:
            unknown.append(key)
            continue
        seen.add(key)
        selected.append(key)

    if unknown:
        raise UnknownVisitRecordExportColumn(
            f"不支持的导出列: {unknown}，可选: {list(VISIT_RECORD_EXPORT_COLUMN_KEYS)}"
        )
    if not selected:
        return list(VISIT_RECORD_EXPORT_COLUMN_KEYS)
    return selected


def get_export_headers(column_keys: Iterable[str], language: str = "zh") -> list[str]:
    is_en = language == "en"
    headers: list[str] = []
    for key in column_keys:
        column = _EXPORT_COLUMN_BY_KEY[key]
        headers.append(column.header_en if is_en else column.header_zh)
    return headers


def pick_export_row_values(
    row: Mapping[str, Any],
    column_keys: Sequence[str],
) -> list[Any]:
    return [row.get(key, "") for key in column_keys]


def build_export_row(item: Any, filing_opportunity_number: str = "", language: str = "zh") -> dict[str, Any]:
    """将跟进记录转为导出列字典（含全部列）。"""
    is_en = language == "en"

    contact_names_str = ""
    if item.contacts and len(item.contacts) > 0:
        contact_names_str = ", ".join([c.name or "" for c in item.contacts if c.name])
    else:
        contact_names_str = item.contact_name or ""

    followup_object_name = item.followup_object_name or ""

    # 历史兼容：旧数据里 account 与 partner 同时有值、external 为空；
    # 导出时按新语义将 partner 映射为 external 协同展示（仅导出视图，不改数据）。
    external_collaboration_partner_name = item.external_collaboration_partner_name or ""
    external_collaboration_partner_id = item.external_collaboration_partner_id or ""
    if (
        (item.account_name or item.account_id)
        and (item.partner_name or item.partner_id)
        and not (external_collaboration_partner_name or external_collaboration_partner_id)
    ):
        external_collaboration_partner_name = item.partner_name or ""
        external_collaboration_partner_id = item.partner_id or ""

    key_fields = [
        str(item.id or ""),
        str(followup_object_name or item.opportunity_name or ""),
        str(item.visit_communication_date or ""),
        str(item.recorder or ""),
        contact_names_str,
        str(item.last_modified_time or ""),
    ]
    key_string = "|".join(key_fields)
    record_id = hashlib.md5(key_string.encode("utf-8")).hexdigest()[:12]

    first_visit_text = "Yes" if item.is_first_visit else "No" if item.is_first_visit is not None else ""
    call_high_text = "Yes" if item.is_call_high else "No" if item.is_call_high is not None else ""
    if not is_en:
        first_visit_text = "是" if item.is_first_visit else "否" if item.is_first_visit is not None else ""
        call_high_text = "是" if item.is_call_high else "否" if item.is_call_high is not None else ""

    followup_record = item.followup_record_en if is_en else item.followup_record_zh
    followup_record = followup_record or item.followup_record or ""

    followup_quality_level = item.followup_quality_level_en if is_en else item.followup_quality_level_zh or ""
    followup_quality_reason = item.followup_quality_reason_en if is_en else item.followup_quality_reason_zh or ""

    next_steps = item.next_steps_en if is_en else item.next_steps_zh
    next_steps = next_steps or item.next_steps or ""

    next_steps_quality_level = item.next_steps_quality_level_en if is_en else item.next_steps_quality_level_zh or ""
    next_steps_quality_reason = item.next_steps_quality_reason_en if is_en else item.next_steps_quality_reason_zh or ""

    raw_assessment_flag = str(item.assessment_flag or "").strip()
    assessment_flag_map = {
        "red": "🔴",
        "yellow": "🟡",
        "green": "🟢",
    }
    assessment_flag = assessment_flag_map.get(raw_assessment_flag.lower(), raw_assessment_flag)

    record_type = ""
    if item.record_type:
        record_type_enum = RecordType.from_english(item.record_type)
        if record_type_enum:
            record_type = record_type_enum.english if is_en else record_type_enum.chinese
        else:
            record_type = item.record_type

    attachment = getattr(item, "attachment", None)
    if attachment:
        location = getattr(attachment, "location", None) or ""
        latitude = getattr(attachment, "latitude", None) or ""
        longitude = getattr(attachment, "longitude", None) or ""
        taken_at = getattr(attachment, "taken_at", None) or ""
    else:
        location = ""
        latitude = ""
        longitude = ""
        taken_at = ""

    contact_positions_str = ""
    contact_names_str = ""
    if item.contacts and len(item.contacts) > 0:
        positions = [c.position or "" for c in item.contacts if c.position]
        names = [c.name or "" for c in item.contacts if c.name]
        contact_positions_str = ", ".join(positions)
        contact_names_str = ", ".join(names)
    else:
        contact_positions_str = item.contact_position or ""
        contact_names_str = item.contact_name or ""

    return {
        "record_id": item.record_id or record_id,
        "customer_level": item.customer_level or "",
        "followup_object_name": followup_object_name,
        "followup_object_id": item.followup_object_id or "",
        "customer_attribute": item.customer_attribute or "",
        "is_first_visit": first_visit_text,
        "is_call_high": call_high_text,
        "external_collaboration_partner_name": external_collaboration_partner_name,
        "external_collaboration_partner_id": external_collaboration_partner_id,
        "opportunity_name": item.opportunity_name or "",
        "opportunity_number": filing_opportunity_number or "",
        "opportunity_id": item.opportunity_id or "",
        "visit_communication_date": item.visit_communication_date or "",
        "recorder": item.recorder or "",
        "department": item.department or "",
        "contact_position": contact_positions_str,
        "contact_name": contact_names_str,
        "collaborative_participants": item.collaborative_participants or "",
        "visit_communication_method": item.visit_communication_method or "",
        "visit_purpose": item.visit_purpose or "",
        "attachment_location": location,
        "attachment_latitude": latitude,
        "attachment_longitude": longitude,
        "attachment_taken_at": taken_at,
        "followup_record": followup_record,
        "followup_quality_level": followup_quality_level,
        "followup_quality_reason": followup_quality_reason,
        "next_steps": next_steps,
        "next_steps_quality_level": next_steps_quality_level,
        "next_steps_quality_reason": next_steps_quality_reason,
        "assessment_flag": assessment_flag,
        "record_type": record_type,
        "visit_type": item.visit_type or "",
        "remarks": item.remarks or "",
        "comments": format_crm_comments_for_export(item.comments, comment_type="comment"),
        "tasks": format_crm_comments_for_export(item.comments, comment_type="task"),
        "last_modified_time": item.last_modified_time or "",
    }
