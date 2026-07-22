import logging
import json
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Any
from datetime import date, datetime
from uuid import UUID
from app.core.config import settings
from app.core.db import get_db_session
from app.api.routes.crm.models import SimpleVisitRecordCreate, CompleteVisitRecordCreate
from app.api.deps import CurrentUserDep, SessionDep
from app.repositories.document_content import DocumentContentRepo
from app.services.platform_notification_service import platform_notification_service
from app.utils.ark_llm import call_ark_llm
from app.utils.uuid6 import uuid6
logger = logging.getLogger(__name__)

_RISK_INFO_FENCE_RE = re.compile(
    r"^\s*```(?:json|text|markdown|md)?\s*\n?(.*?)\n?\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)

def _safe_parse_json_object(raw: str) -> dict:
    """
    JSON 模式下的最小解析：
    - 仅接受可直接解析的 JSON 对象
    - 避免对异常输出做激进修复，减少误判
    """
    parsed = json.loads((raw or "").strip())
    if not isinstance(parsed, dict):
        raise ValueError("Parsed JSON is not an object")
    return parsed


def _should_generate_multilingual_content() -> bool:
    """
    是否启用多语内容生成。
    默认关闭；仅当显式开启且包含 zh/en 时才启用，便于后续扩展更多语种。
    """
    if not settings.CRM_VISIT_RECORD_MULTILINGUAL_ENABLED:
        return False

    langs = settings.CRM_VISIT_RECORD_MULTILINGUAL_LANGS
    if not langs:
        # 开关已开但未指定语言时，默认沿用 zh/en
        return True

    normalized = {str(lang).strip().lower() for lang in langs if str(lang).strip()}
    return "zh" in normalized and "en" in normalized


def _generate_record_id(record_type, now):
    dt = now.strftime("%Y%m%d_%H%M%S")
    # 使用毫秒（微秒的前3位）即可，配合随机部分足够保证唯一性
    millisecond = f"{now.microsecond // 1000:03d}"  # 微秒转毫秒，范围 0-999
    rand = uuid6().hex[:8]
    return f"{record_type}_{dt}_{millisecond}_{rand}"

def _process_field_value_for_db(field_name: str, value: Any) -> Any:
    """
    处理表单提交的字段值，转换为数据库存储格式
    只处理需要特殊转换的字段，其他字段直接返回
    """
    if value is None or value == "":
        return None
    
    # 处理附件字段
    if field_name == 'attachment':
        if isinstance(value, dict):
            # 结构化 JSON，序列化为 JSON 字符串
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        elif isinstance(value, str):
            # 字符串（base64 / URL / JSON 字符串），直接返回
            return value
        return str(value) if value else None
    
    # 处理协同参与人字段
    if field_name == 'collaborative_participants':
        if isinstance(value, list):
            # 如果是列表，转换为JSON字符串存储
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        elif isinstance(value, str):
            # 如果是字符串，直接返回（可能是旧格式或已经是JSON字符串）
            return value
        elif isinstance(value, dict):
            # 如果是字典，转换为JSON字符串
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value) if value else None
    
    # 处理时间字段（visit_start_time, visit_end_time）
    if field_name in ['visit_start_time', 'visit_end_time']:
        if isinstance(value, str):
            return value
        elif hasattr(value, 'strftime'):  # datetime类型
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return value
    
    # 其他字段直接返回
    return value


# 保存表单拜访记录到 crm_sales_visit_records
def save_visit_record_to_crm_table(record_schema: SimpleVisitRecordCreate | CompleteVisitRecordCreate, db_session: SessionDep):
    """
    保存拜访记录到数据库
    直接使用数据库字段名，不再进行飞书字段名转换
    """
    now = datetime.now()
    batch_time = datetime.now()
    
    # 获取所有字段（排除None值）
    fields = record_schema.model_dump(exclude_none=True)
    # 调用方只传 followup_object_* 时，回填 account/partner 供下游兼容读取
    from app.utils.crm_followup_object import apply_followup_object_legacy_dual_write

    apply_followup_object_legacy_dual_write(fields)

    # 生成record_id
    record_id = _generate_record_id(record_schema.visit_type, now)
    
    # 构建数据库字段映射，直接使用数据库字段名
    mapped = {}
    
    # 处理需要特殊转换的字段
    special_fields = ['attachment', 'collaborative_participants', 'visit_start_time', 'visit_end_time']
    for field_name in special_fields:
        if field_name in fields:
            mapped[field_name] = _process_field_value_for_db(field_name, fields[field_name])
    
    # 处理recorder_id字段：确保转换为不带连字符的UUID字符串格式（32字符）
    # 在TiDB/MySQL中，GUID类型存储为不带连字符的32字符字符串
    recorder_uuid_obj: Optional[UUID] = None
    if 'recorder_id' in fields and fields['recorder_id'] not in ("", None):
        try:
            recorder_id_value = fields['recorder_id']
            # 统一转换为UUID对象，然后转换为不带连字符的32字符字符串格式
            if isinstance(recorder_id_value, str):
                # 验证并标准化UUID字符串格式
                uuid_obj = UUID(recorder_id_value)
                recorder_uuid_obj = uuid_obj
                uuid_str = uuid_obj.hex  # 转换为不带连字符的32字符格式
                # 标准UUID hex字符串应该是32字符
                if len(uuid_str) != 32:
                    logger.error(f"recorder_id string length incorrect: {len(uuid_str)} chars (expected 32): {uuid_str}")
                    raise ValueError(f"Invalid UUID hex string length: {len(uuid_str)}")
                mapped['recorder_id'] = uuid_str
            elif isinstance(recorder_id_value, UUID):
                # 如果已经是UUID对象，转换为不带连字符的hex字符串
                recorder_uuid_obj = recorder_id_value
                uuid_str = recorder_id_value.hex
                if len(uuid_str) != 32:
                    logger.error(f"recorder_id string length incorrect: {len(uuid_str)} chars (expected 32): {uuid_str}")
                    raise ValueError(f"Invalid UUID hex string length: {len(uuid_str)}")
                mapped['recorder_id'] = uuid_str
            else:
                # 其他类型尝试转换为UUID再转换为hex字符串
                uuid_obj = UUID(str(recorder_id_value))
                recorder_uuid_obj = uuid_obj
                uuid_str = uuid_obj.hex
                if len(uuid_str) != 32:
                    logger.error(f"recorder_id string length incorrect: {len(uuid_str)} chars (expected 32): {uuid_str}")
                    raise ValueError(f"Invalid UUID hex string length: {len(uuid_str)}")
                mapped['recorder_id'] = uuid_str
            logger.debug(f"Converted recorder_id to UUID hex string: {mapped['recorder_id']} (length: {len(mapped['recorder_id'])})")
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to convert recorder_id to UUID hex string: {fields['recorder_id']}, error: {e}")
            # 如果转换失败，跳过该字段，让数据库使用默认值或报错
            pass

    # 记录人部门快照：在写入时固化，供后续指标统计与下游直查使用
    try:
        if recorder_uuid_obj:
            from app.repositories.user_department_relation import user_department_relation_repo
            from app.repositories.department_mirror import department_mirror_repo

            user_id_str = str(recorder_uuid_obj)  # uuid36
            dept_id = user_department_relation_repo.get_primary_department_by_user_ids(db_session, [user_id_str]).get(user_id_str)
            if dept_id:
                dept_name = department_mirror_repo.get_department_name_by_id(db_session, dept_id)
                mapped["recorder_department_id"] = dept_id
                mapped["recorder_department_name"] = dept_name
            else:
                # 兜底：未能解析部门时，写入 UNKNOWN，避免后续统计/下游直查出现 NULL 维度
                mapped["recorder_department_id"] = "UNKNOWN"
                mapped["recorder_department_name"] = ""
    except Exception as e:
        # 快照字段不应阻塞主流程
        logger.warning(f"Failed to snapshot recorder department: {e}")
    
    # 处理其他字段（直接使用，但需要过滤空值）
    for field_name, field_value in fields.items():
        if field_name not in special_fields and field_name not in ['contacts', 'latitude', 'longitude', 'form_type', 'recorder_id']:
            if field_value not in ("", None):
                mapped[field_name] = field_value
    
    # 处理多个联系人字段
    contacts_list = None
    if isinstance(record_schema, CompleteVisitRecordCreate):
        # 如果提供了contacts字段，优先使用
        if record_schema.contacts:
            contacts_list = [contact.model_dump(exclude_none=True) for contact in record_schema.contacts]
        # 否则，如果提供了旧的单个联系人字段，构造联系人列表
        elif record_schema.contact_name or record_schema.contact_position or record_schema.contact_id:
            contact_dict = {}
            if record_schema.contact_name:
                contact_dict['name'] = record_schema.contact_name
            if record_schema.contact_position:
                contact_dict['position'] = record_schema.contact_position
            if record_schema.contact_id:
                contact_dict['contact_id'] = record_schema.contact_id
            if contact_dict:
                contacts_list = [contact_dict]
    
    # 保存contacts字段（JSON格式）
    if contacts_list:
        mapped['contacts'] = contacts_list
    
    # 处理经纬度字段
    if 'latitude' in fields:
        mapped['latitude'] = fields['latitude']
    if 'longitude' in fields:
        mapped['longitude'] = fields['longitude']
    
    # 设置必需字段
    mapped['record_id'] = record_id
    mapped['last_modified_time'] = batch_time
    
    # 使用事务保存
    from app.tasks.bitable_import import CRM_TABLE
    from sqlalchemy import MetaData, Table, text
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    
    metadata = MetaData()
    crm_table = Table(CRM_TABLE, metadata, autoload_with=db_session.bind)
    
    insert_stmt = mysql_insert(crm_table).values(**mapped)
    update_stmt = {k: mapped[k] for k in mapped if k != 'record_id'}
    if mapped.get('account_id') in (None, '', 'null'):
        update_stmt['account_id'] = text('account_id')
    ondup_stmt = insert_stmt.on_duplicate_key_update(**update_stmt)
    db_session.execute(ondup_stmt)
    # 不在这里commit，由调用方控制事务
    
    # 返回record_id和实际保存的时间
    return record_id, batch_time

def extract_followup_record_and_next_steps(followup_content: str) -> tuple[str, str]:
    """
    从跟进内容中提取followup_record（跟进记录）和next_steps（下一步计划）
    
    Args:
        followup_content: 用户输入的跟进内容
        
    Returns:
        tuple: (followup_record, next_steps) 提取出的跟进记录和下一步计划，保持与原文一致的语言
    """
    prompt = f"""
You are a sales management expert who needs to extract two parts from sales personnel's follow-up content: follow-up record (followup_record) and next steps (next_steps).

Please analyze the following content and divide it into two parts:

1. **Follow-up Record (followup_record)**: Describe the specific content of this visit/communication, customer feedback, communication process, etc.
2. **Next Steps (next_steps)**: Specific follow-up action plans, including time arrangements, specific actions, risk management, etc.

Please output strictly in the following JSON format:
{{
  "followup_record": "follow-up record content",
  "next_steps": "next steps content"
}}

**Important**: Please maintain the original language of the input content. If the input is in Chinese, respond in Chinese. If the input is in English, respond in English. If the input is mixed, use the predominant language.

**Extraction Rules**:
- If the content clearly contains keywords related to next steps, plans, arrangements, etc., classify them as next_steps
- If the content contains risk-related content (such as risks, problems, concerns, challenges, difficulties, obstacles, threats, uncertainties, etc.), classify them as next_steps
- If the content describes things that have already happened, customer feedback, communication processes, etc., classify them as followup_record
- If it cannot be clearly distinguished, use most of the content as followup_record, and use parts containing time arrangements, specific actions, or risk management as next_steps
- If the content is very short or cannot be separated, use the entire content as followup_record, and set next_steps to empty string

**Risk Content Identification**:
- Contains keywords related to risks, problems, concerns, challenges, difficulties, obstacles, threats, uncertainties, etc.
- Describes customer concerns, technical difficulties, business risks, competitive pressure, etc.
- Items that require follow-up, resolution, or monitoring

Content to analyze:
{followup_content}
"""
    
    try:
        result = call_ark_llm(
            prompt,
            temperature=0,
            # response_format={"type": "json_object"},
        )
        data = json.loads(result)
        followup_record = data.get("followup_record", followup_content)
        next_steps = data.get("next_steps", "")
        
        # 如果提取失败或结果为空，返回原始内容作为progress
        if not followup_record:
            followup_record = followup_content
        if not next_steps:
            next_steps = ""
            
        return followup_record, next_steps
        
    except Exception as e:
        logger.warning(f"Failed to extract progress and next_steps: {e}")
        # 如果AI提取失败，返回原始内容作为followup_record，next_steps为空
        return followup_content, ""


def _safe_strip_field_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


def _is_blank_field_value(v) -> bool:
    return not _safe_strip_field_value(v)


def _merge_visit_record_snapshot_from_db(snapshot: dict, db_row: Any) -> None:
    """推送前从库表补全快照中缺失的字段（不覆盖已有非空值）。"""
    if not db_row:
        return
    for attr in (
        "account_name",
        "account_id",
        "partner_name",
        "partner_id",
        "followup_object_type",
        "followup_object_id",
        "followup_object_name",
        "external_collaboration_partner_name",
        "external_collaboration_partner_id",
    ):
        if _is_blank_field_value(snapshot.get(attr)):
            db_val = getattr(db_row, attr, None)
            if not _is_blank_field_value(db_val):
                snapshot[attr] = db_val


def fill_sales_visit_record_fields(sales_visit_record, db_session):
    from app.utils.crm_followup_object import resolve_followup_object_from_record

    # 拜访对象展示：纯线索/伙伴拜访时无客户名，用跟进对象名填充 account_name 供卡片展示
    account_name = _safe_strip_field_value(sales_visit_record.get("account_name"))
    partner_name = _safe_strip_field_value(sales_visit_record.get("partner_name"))
    followup_obj = resolve_followup_object_from_record(sales_visit_record)
    if account_name:
        sales_visit_record["account_name"] = account_name
    elif followup_obj and followup_obj.object_name:
        sales_visit_record["account_name"] = followup_obj.object_name
    elif partner_name:
        sales_visit_record["account_name"] = partner_name

    if partner_name:
        sales_visit_record["partner_name"] = partner_name

    ext_name = _safe_strip_field_value(sales_visit_record.get("external_collaboration_partner_name"))
    sales_visit_record["external_collaboration_partner_name"] = ext_name or None

    # 处理是否首次拜访字段
    is_first_visit = sales_visit_record.get("is_first_visit")
    sales_visit_record["is_first_visit"] = "首次拜访" if is_first_visit else None
    sales_visit_record["is_first_visit_en"] = "first visit" if is_first_visit else None
    
    # 处理是否call high字段
    is_call_high = sales_visit_record.get("is_call_high")
    sales_visit_record["is_call_high"] = "关键决策人拜访" if is_call_high else None
    sales_visit_record["is_call_high_en"] = "call high" if is_call_high else None
    
    # 处理联系人字段：将contacts转换为格式化文本 "姓名1（职位1）\n姓名2（职位2）"
    contacts = sales_visit_record.get("contacts")
    contact_info_parts = []
    has_contacts_field = contacts is not None  # 标记是否明确提供了contacts字段

    if contacts:
        # 如果提供了contacts字段（列表格式）
        if isinstance(contacts, list):
            for contact in contacts:
                if isinstance(contact, dict):
                    name = _safe_strip_field_value(contact.get("name"))
                    position = _safe_strip_field_value(contact.get("position"))
                    if name:
                        if position:
                            contact_info_parts.append(f"{name}（{position}）")
                        else:
                            contact_info_parts.append(name)
                elif hasattr(contact, "name") and hasattr(contact, "position"):
                    # 如果是Contact对象
                    name = (contact.name or "").strip()
                    position = (contact.position or "").strip()
                    if name:
                        if position:
                            contact_info_parts.append(f"{name}（{position}）")
                        else:
                            contact_info_parts.append(name)
    
    # 如果没有contacts字段（不是空列表，而是字段不存在），尝试从旧字段构造
    if not has_contacts_field and not contact_info_parts:
        contact_name = _safe_strip_field_value(sales_visit_record.get("contact_name"))
        contact_position = _safe_strip_field_value(sales_visit_record.get("contact_position"))
        if contact_name:
            if contact_position:
                contact_info_parts.append(f"{contact_name}（{contact_position}）")
            else:
                contact_info_parts.append(contact_name)
    
    # 将格式化后的联系人信息保存到contacts字段（用于推送消息显示）
    if contact_info_parts:
        sales_visit_record["contacts"] = "\n".join(contact_info_parts)
    elif has_contacts_field:
        # 如果明确提供了contacts字段但为空，设置为None
        sales_visit_record["contacts"] = None
    
    # 处理附件字段：避免将历史的 base64 大字段推送到通知中
    attachment = sales_visit_record.get("attachment")
    if attachment:
        # 字符串（可能是base64 / URL / JSON字符串）一律不直接透传，推送里丢弃
        if isinstance(attachment, str):
            sales_visit_record["attachment"] = None
        # 如果是 dict，仅保留轻量字段，去掉url字段
        elif isinstance(attachment, dict):
          sanitized = dict(attachment)
          url_val = sanitized.get("url")
          if isinstance(url_val, str) and not (url_val.startswith(settings.STORAGE_PATH_PREFIX) or url_val.startswith("http://") or url_val.startwith("https://")):
              sanitized.pop("url", None)  # url 里如果不是链接（可能是base64）就删掉
          sales_visit_record["attachment"] = sanitized
    
    # 添加字段名映射，用于卡片展示
    from app.services.crm_config_service import add_field_mapping_to_data
    sales_visit_record = add_field_mapping_to_data(sales_visit_record, db_session, "拜访记录")
    
    # 后向兼容：为旧字段赋值对应的中文值
    if sales_visit_record.get("followup_quality_level_zh") is not None:
        sales_visit_record["followup_quality_level"] = sales_visit_record["followup_quality_level_zh"]
    
    if sales_visit_record.get("followup_quality_reason_zh") is not None:
        sales_visit_record["followup_quality_reason"] = sales_visit_record["followup_quality_reason_zh"]
    
    if sales_visit_record.get("next_steps_quality_level_zh") is not None:
        sales_visit_record["next_steps_quality_level"] = sales_visit_record["next_steps_quality_level_zh"]
    
    if sales_visit_record.get("next_steps_quality_reason_zh") is not None:
        sales_visit_record["next_steps_quality_reason"] = sales_visit_record["next_steps_quality_reason_zh"]


    # 处理subject和subject_en字段 - 利用VisitSubject枚举的中英文支持
    from app.api.routes.crm.models import VisitSubject, RecordType
    
    original_subject = sales_visit_record.get("subject")
    
    if original_subject is None or original_subject == "":
        sales_visit_record["subject"] = None
        sales_visit_record["subject_en"] = None
    else:
        # 尝试根据英文值查找枚举
        subject_enum = VisitSubject.from_english(original_subject)
        if subject_enum:
            # 原始值是英文，设置subject为中文，subject_en为英文
            sales_visit_record["subject"] = subject_enum.chinese
            sales_visit_record["subject_en"] = subject_enum.english
        else:
            # 尝试根据中文值查找枚举
            subject_enum = VisitSubject.from_chinese(original_subject)
            if subject_enum:
                # 原始值是中文，设置subject为中文，subject_en为英文
                sales_visit_record["subject"] = subject_enum.chinese
                sales_visit_record["subject_en"] = subject_enum.english
            else:
                # 原始值不在枚举中，保持原值
                sales_visit_record["subject"] = original_subject
                sales_visit_record["subject_en"] = original_subject

    # 其他字段（排除特殊处理的字段和动态字段）
    skip_none_to_dash = {
        "is_first_visit",
        "is_first_visit_en",
        "is_call_high",
        "is_call_high_en",
        "subject",
        "subject_en",
        "visit_start_time",
        "visit_end_time",
        "record_type",
        "visit_purpose",
    }
    for k, v in sales_visit_record.items():
        if v is None and k not in skip_none_to_dash:
            sales_visit_record[k] = "--"
    return sales_visit_record


def generate_dynamic_fields_for_visit_record(sales_visit_record):
    """
    为拜访记录生成动态字段数组
    
    Args:
        sales_visit_record: 拜访记录数据
        
    Returns:
        dynamic_fields数组
    """
    try:
        from app.crm.dynamic_fields import generate_dynamic_fields_array
        
        # 生成动态字段数组
        dynamic_fields_array = generate_dynamic_fields_array(sales_visit_record)
        logger.debug(f"生成动态字段数组: {dynamic_fields_array}")
        return dynamic_fields_array
        
    except Exception as e:
        logger.warning(f"生成动态字段数组失败: {e}")
        return []


def _build_visit_record_detail_url(record_id: Optional[str]) -> str:
    from app.utils.push_page_urls import build_visit_record_billing_page_url

    return build_visit_record_billing_page_url(str(record_id or "").strip())


def report_visit_record_billing(operator_user_id: UUID, record_id: Optional[str]) -> None:
    """拜访卡片推送成功后的计费上报（供 /notification/push 等调用）。"""
    from app.services.feishu_billing_facade import BillingScenario, report_billing_usage

    rid = (str(record_id).strip() if record_id is not None else "") or ""
    review_detail = _build_visit_record_detail_url(rid if rid else None)
    if rid:
        report_billing_usage(
            BillingScenario.VISIT_RECORD,
            review_detail=review_detail,
            trace_key=f"visit-record:{rid}",
            operator_user_id=operator_user_id,
            log_context=f"record_id={rid}",
        )
    else:
        logger.info(
            "Visit billing with empty record_id: deterministic trace skipped, using random trace_id"
        )
        report_billing_usage(
            BillingScenario.VISIT_RECORD,
            review_detail=review_detail,
            operator_user_id=operator_user_id,
            log_context="record_id_empty_random_trace",
        )


def _load_visit_record_push_snapshot(
    db_session: Any,
    record_id: str,
    visit_snapshot: Optional[dict],
) -> tuple[Optional[dict], Optional[str], Optional[UUID], Optional[datetime]]:
    """加载推送所需快照；visit_type / recorder_id / saved_time 从库表补全。"""
    from sqlmodel import select
    from app.models.crm_sales_visit_records import CRMSalesVisitRecord

    row = db_session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
    ).first()
    if not row:
        return None, None, None, None

    snapshot = dict(visit_snapshot) if visit_snapshot else _crm_visit_record_row_to_push_dict(row)
    visit_type = (row.visit_type or "form").strip() or "form"
    recorder_id = row.recorder_id
    saved_time = row.last_modified_time
    return snapshot, visit_type, recorder_id, saved_time


VISIT_RECORD_REVISED_NOTICE = "【修改后】"


def _update_revision_card_push_status(
    db_session: Any,
    *,
    record_id: str,
    revision_seq: Optional[int],
    status: str,
) -> None:
    if revision_seq is None:
        return
    try:
        from app.repositories.visit_record_revisions import visit_record_revisions_repo

        visit_record_revisions_repo.update_card_push_status(
            db_session,
            record_id=record_id,
            revision_seq=revision_seq,
            card_push_status=status,
            commit=True,
        )
    except Exception as exc:
        logger.warning(
            "Failed to update revision card_push_status, record_id=%s rev=%s: %s",
            record_id,
            revision_seq,
            exc,
        )


def fallback_push_visit_record_card(
    record_id: str,
    *,
    db_session: Any,
    visit_snapshot: Optional[dict] = None,
    visit_type: Optional[str] = None,
    meeting_notes: Optional[str] = None,
    risk_info: Optional[str] = None,
    saved_time: Optional[datetime] = None,
    operator_user_id: Optional[UUID] = None,
    is_revised: bool = False,
    revision_seq: Optional[int] = None,
) -> bool:
    """
    Aldebaran 不可用或通知失败时，使用空任务列表本地推送拜访卡片。
    """
    from app.services.visit_record_card_push_status import (
        VisitRecordCardPushStatus,
        update_visit_record_card_push_delivery,
        update_visit_record_card_push_status,
    )

    snapshot, vt, recorder_id, row_saved_time = _load_visit_record_push_snapshot(
        db_session, record_id, visit_snapshot
    )
    if not snapshot:
        logger.error("Fallback card push skipped, record not found: %s", record_id)
        return False

    visit_type = visit_type or vt or "form"
    saved_time = saved_time or row_saved_time
    billing_user_id = operator_user_id or recorder_id

    if visit_type == "link" and (meeting_notes is None or risk_info is None):
        try:
            from app.repositories.document_content import DocumentContentRepo

            doc = DocumentContentRepo().get_by_visit_record_id(db_session, record_id)
            if doc:
                if meeting_notes is None:
                    meeting_notes = (doc.meeting_summary or "").strip() or None
                if risk_info is None:
                    risk_info = (doc.risk_info or "").strip() or None
        except Exception as exc:
            logger.warning(
                "Failed to load document_content for fallback card push, record_id=%s: %s",
                record_id,
                exc,
            )

    push_result = push_visit_record_message(
        record_id=record_id,
        sales_visit_record=snapshot,
        visit_type=visit_type,
        db_session=db_session,
        meeting_notes=meeting_notes,
        risk_info=risk_info,
        saved_time=saved_time,
        tasks=[],
        task_count=0,
        is_revised=is_revised,
    )
    status = push_result["card_push_status"]
    update_visit_record_card_push_delivery(
        db_session,
        record_id,
        status,
        failed_recipients=push_result.get("failed_recipients"),
        total_recipients=push_result.get("recipients_count"),
        commit=True,
    )
    _update_revision_card_push_status(
        db_session,
        record_id=record_id,
        revision_seq=revision_seq,
        status=status,
    )
    if status in {
        VisitRecordCardPushStatus.PUSHED,
        VisitRecordCardPushStatus.PARTIAL_PUSHED,
    } and billing_user_id:
        try:
            report_visit_record_billing(billing_user_id, record_id)
        except Exception as exc:
            logger.error(
                "Visit billing after fallback card push failed, record_id=%s: %s",
                record_id,
                exc,
                exc_info=True,
            )
    logger.info(
        "Fallback visit card push record_id=%s status=%s",
        record_id,
        status,
    )
    return status != VisitRecordCardPushStatus.FAILED


def _notify_aldebaran_visit_record_post_process_impl(
    record_id: str,
    visit_snapshot: Optional[dict],
    db_session: Any,
    *,
    operator_user_id: Optional[UUID] = None,
    visit_type: Optional[str] = None,
    meeting_notes: Optional[str] = None,
    risk_info: Optional[str] = None,
    saved_time: Optional[datetime] = None,
    message_type: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    payload: Optional[dict] = None,
    trace_id: Optional[str] = None,
    is_revised: bool = False,
    revision_seq: Optional[int] = None,
) -> bool:
    from app.services.visit_record_card_push_status import (
        VisitRecordCardPushStatus,
        update_visit_record_card_push_delivery,
        update_visit_record_card_push_status,
    )

    update_visit_record_card_push_status(
        db_session,
        record_id,
        VisitRecordCardPushStatus.PENDING,
        commit=True,
    )
    if is_revised and revision_seq is not None:
        _update_revision_card_push_status(
            db_session,
            record_id=record_id,
            revision_seq=revision_seq,
            status=VisitRecordCardPushStatus.PENDING,
        )

    if not settings.ALDEBARAN_VISIT_RECORD_POST_PROCESS_ENABLED:
        logger.info(
            "Aldebaran post-process disabled, fallback local card push, record_id=%s revised=%s",
            record_id,
            is_revised,
        )
        return fallback_push_visit_record_card(
            record_id,
            db_session=db_session,
            visit_snapshot=visit_snapshot,
            visit_type=visit_type,
            meeting_notes=meeting_notes,
            risk_info=risk_info,
            saved_time=saved_time,
            operator_user_id=operator_user_id,
            is_revised=is_revised,
            revision_seq=revision_seq,
        )

    try:
        from app.services.aldebaran_service import aldebaran_client

        aldebaran_client.trigger_visit_record_post_process(
            record_id=record_id,
            event_time=saved_time,
            message_type=message_type,
            dedupe_key=dedupe_key,
            payload=payload,
            trace_id=trace_id,
        )
        update_visit_record_card_push_status(
            db_session,
            record_id,
            VisitRecordCardPushStatus.AWAITING_CALLBACK,
            commit=True,
        )
        if is_revised and revision_seq is not None:
            _update_revision_card_push_status(
                db_session,
                record_id=record_id,
                revision_seq=revision_seq,
                status=VisitRecordCardPushStatus.AWAITING_CALLBACK,
            )
        logger.info(
            "Triggered Aldebaran visit post-process, record_id=%s message_type=%s",
            record_id,
            message_type or settings.ALDEBARAN_VISIT_RECORD_MESSAGE_TYPE,
        )
        return True
    except Exception as exc:
        logger.error(
            "Aldebaran post-process failed, fallback local card push, record_id=%s: %s",
            record_id,
            exc,
            exc_info=True,
        )

    return fallback_push_visit_record_card(
        record_id,
        db_session=db_session,
        visit_snapshot=visit_snapshot,
        visit_type=visit_type,
        meeting_notes=meeting_notes,
        risk_info=risk_info,
        saved_time=saved_time,
        operator_user_id=operator_user_id,
        is_revised=is_revised,
        revision_seq=revision_seq,
    )


def _notify_aldebaran_visit_record_saved_impl(
    record_id: str,
    visit_snapshot: Optional[dict],
    db_session: Any,
    *,
    operator_user_id: Optional[UUID] = None,
    visit_type: Optional[str] = None,
    meeting_notes: Optional[str] = None,
    risk_info: Optional[str] = None,
    saved_time: Optional[datetime] = None,
) -> bool:
    return _notify_aldebaran_visit_record_post_process_impl(
        record_id,
        visit_snapshot,
        db_session,
        operator_user_id=operator_user_id,
        visit_type=visit_type,
        meeting_notes=meeting_notes,
        risk_info=risk_info,
        saved_time=saved_time,
    )


def _notify_aldebaran_visit_record_revised_impl(
    record_id: str,
    db_session: Any,
    *,
    revision_seq: int,
    revised_by_user_id: UUID,
    changes: list[dict],
    operator_user_id: Optional[UUID] = None,
    visit_type: Optional[str] = None,
    saved_time: Optional[datetime] = None,
) -> bool:
    message_type = settings.ALDEBARAN_VISIT_RECORD_REVISED_MESSAGE_TYPE
    dedupe_key = f"{message_type}:{record_id}:rev:{revision_seq}"
    payload = {
        "record_id": record_id,
        "revision_seq": revision_seq,
        "revised_by_user_id": str(revised_by_user_id),
        "changes": changes,
    }
    return _notify_aldebaran_visit_record_post_process_impl(
        record_id,
        None,
        db_session,
        operator_user_id=operator_user_id or revised_by_user_id,
        visit_type=visit_type,
        saved_time=saved_time,
        message_type=message_type,
        dedupe_key=dedupe_key,
        payload=payload,
        trace_id=f"{record_id}:rev:{revision_seq}",
        is_revised=True,
        revision_seq=revision_seq,
        )


def notify_aldebaran_visit_record_revised(
    record_id: str,
    *,
    revision_seq: int,
    revised_by_user_id: UUID,
    changes: list[dict],
    db_session: Any = None,
    operator_user_id: Optional[UUID] = None,
    visit_type: Optional[str] = None,
    saved_time: Optional[datetime] = None,
) -> bool:
    """拜访记录修订后通知 Aldebaran（crm.visit_record.revised），等待回调推卡。"""
    if db_session is not None:
        return _notify_aldebaran_visit_record_revised_impl(
            record_id,
            db_session,
            revision_seq=revision_seq,
            revised_by_user_id=revised_by_user_id,
            changes=changes,
            operator_user_id=operator_user_id,
            visit_type=visit_type,
            saved_time=saved_time,
        )

    from sqlmodel import Session
    from app.core.db import engine_transactional

    with Session(engine_transactional, expire_on_commit=False) as session:
        return _notify_aldebaran_visit_record_revised_impl(
            record_id,
            session,
            revision_seq=revision_seq,
            revised_by_user_id=revised_by_user_id,
            changes=changes,
            operator_user_id=operator_user_id,
            visit_type=visit_type,
            saved_time=saved_time,
        )


def notify_aldebaran_visit_record_saved(
    record_id: str,
    visit_snapshot: Optional[dict] = None,
    *,
    db_session: Any = None,
    operator_user_id: Optional[UUID] = None,
    visit_type: Optional[str] = None,
    meeting_notes: Optional[str] = None,
    risk_info: Optional[str] = None,
    saved_time: Optional[datetime] = None,
) -> bool:
    """
    拜访记录落库后通知 Aldebaran 做后处理；成功则等待回调推卡。
    通知失败、未启用或接口未实现时，降级为空任务列表本地推卡。
    """
    if db_session is not None:
        return _notify_aldebaran_visit_record_saved_impl(
            record_id,
            visit_snapshot,
            db_session,
            operator_user_id=operator_user_id,
            visit_type=visit_type,
            meeting_notes=meeting_notes,
            risk_info=risk_info,
            saved_time=saved_time,
        )

    from sqlmodel import Session
    from app.core.db import engine_transactional

    with Session(engine_transactional, expire_on_commit=False) as session:
        return _notify_aldebaran_visit_record_saved_impl(
            record_id,
            visit_snapshot,
            session,
            operator_user_id=operator_user_id,
            visit_type=visit_type,
            meeting_notes=meeting_notes,
            risk_info=risk_info,
            saved_time=saved_time,
        )


def _crm_visit_record_row_to_push_dict(row: Any) -> dict:
    """将 ORM 行转为 push_visit_record_message 可用的 dict。"""
    data = row.model_dump()
    for key, value in list(data.items()):
        if isinstance(value, UUID):
            data[key] = str(value)
        elif isinstance(value, datetime):
            data[key] = value.isoformat()
        elif isinstance(value, date):
            data[key] = value.isoformat()
    return data


def push_visit_record_message(
    record_id: str,
    sales_visit_record,
    visit_type,
    db_session=None,
    meeting_notes=None,
    risk_info=None,
    saved_time=None,
    tasks=None,
    task_count=None,
    is_revised: bool = False,
    *,
    retry_failed_recipients=None,
    card_push_total_recipients: Optional[int] = None,
):
    try:
        # 如果没有传入db_session，则创建一个新的
        should_close_session = False
        if db_session is None:
            db_session = get_db_session()
            should_close_session = True
        
        db_row = None
        try:
            from sqlmodel import select
            from app.models.crm_sales_visit_records import CRMSalesVisitRecord

            stmt = select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
            db_row = db_session.exec(stmt).first()
            if db_row:
                _merge_visit_record_snapshot_from_db(sales_visit_record, db_row)
        except Exception as e:
            logger.warning("Failed to load visit record row before card fill, record_id=%s: %s", record_id, e)

        sales_visit_record = fill_sales_visit_record_fields(sales_visit_record, db_session)

        # 补充记录人部门快照信息（从已落库的拜访记录中读取），供后续部门群匹配使用
        try:
            if not db_row:
                logger.debug("No CRMSalesVisitRecord found when enriching department snapshot, record_id=%s", record_id)
            else:
                before_id = sales_visit_record.get("recorder_department_id")
                before_name = sales_visit_record.get("recorder_department_name")
                # 仅在上游未显式提供时补充，避免覆盖调用方传入的数据
                if "recorder_department_id" not in sales_visit_record:
                    sales_visit_record["recorder_department_id"] = db_row.recorder_department_id
                if "recorder_department_name" not in sales_visit_record:
                    sales_visit_record["recorder_department_name"] = db_row.recorder_department_name
                logger.info(
                    "Enriched visit record department snapshot: record_id=%s, before=(%s,%s), after=(%s,%s)",
                    record_id,
                    before_id,
                    before_name,
                    sales_visit_record.get("recorder_department_id"),
                    sales_visit_record.get("recorder_department_name"),
                )
        except Exception as e:
            logger.warning(f"Failed to enrich visit record with department snapshot: {e}")
        
        # 处理时间字段：将saved_time转换为本地时区字符串
        from app.utils.date_utils import convert_utc_to_local_timezone
        
        # 确定要使用的时间
        time_to_use = saved_time or sales_visit_record.get("last_modified_time") or datetime.now()
        
        # 转换为本地时区字符串并保存到last_modified_time字段
        sales_visit_record["last_modified_time"] = convert_utc_to_local_timezone(time_to_use)
        
        # 获取记录人信息
        recorder_id = sales_visit_record.get("recorder_id")
        recorder_name = sales_visit_record.get("recorder")
        
        if not recorder_id and not recorder_name:
            logger.warning("No recorder_id or recorder_name found in sales visit record")
            from app.services.visit_record_card_push_status import (
                VisitRecordCardPushStatus,
            )

            return {
                "success": False,
                "message": "No recorder found",
                "recipients_count": 0,
                "success_count": 0,
                "failed_recipients": [],
                "card_push_status": VisitRecordCardPushStatus.FAILED,
            }
        
        # 确保会议纪要不为空
        if meeting_notes is None or meeting_notes == "":
            meeting_notes = "--"
        # 如果配置了自定义字体大小token，则添加到会议纪要中，主要用于钉钉卡片中设置字体大小
        if settings.CUSTOM_FONT_SIZE_TOKEN:
            meeting_notes =f"<font sizeToken={settings.CUSTOM_FONT_SIZE_TOKEN}>{meeting_notes}</font>"

        # 发送拜访记录通知
        send_kwargs: dict[str, Any] = {
            "db_session": db_session,
            "record_id": record_id,
            "recorder_name": recorder_name,
            "recorder_id": recorder_id,
            "visit_record": sales_visit_record,
            "visit_type": visit_type,
            "meeting_notes": meeting_notes,
            "risk_info": risk_info,
            "tasks": tasks,
            "task_count": task_count,
            "is_revised": is_revised,
        }
        if retry_failed_recipients:
            from app.services.visit_record_card_push_status import (
                failed_recipients_to_recipients_by_platform,
            )

            recipients_override = failed_recipients_to_recipients_by_platform(
                retry_failed_recipients
            )
            if db_session is not None:
                recipients_override = platform_notification_service._filter_recipients_by_active_profiles(
                    db_session, recipients_override
                )
            send_kwargs.update(
                {
                    "recipients_by_platform_override": recipients_override,
                    "skip_group_notifications": True,
                    "total_recipients_count_override": card_push_total_recipients,
                    "previously_failed_count": len(retry_failed_recipients),
                }
            )
        result = platform_notification_service.send_visit_record_notification(**send_kwargs)
        
        if result["success"]:
            logger.info(f"Successfully pushed visit record notification: {result['message']}")
        else:
            logger.warning(f"Failed to push visit record notification: {result['message']}")

        return result

    except Exception as e:
        logger.error(f"发送拜访记录通知失败: {e}")
        from app.services.visit_record_card_push_status import VisitRecordCardPushStatus

        return {
            "success": False,
            "message": str(e),
            "recipients_count": 0,
            "success_count": 0,
            "failed_recipients": [],
            "card_push_status": VisitRecordCardPushStatus.FAILED,
        }
    finally:
        # 只有当我们创建了session时才关闭它
        if should_close_session:
            db_session.close()


def _extract_contact_info_from_record(record: SimpleVisitRecordCreate | CompleteVisitRecordCreate) -> tuple[Optional[str], Optional[str]]:
    """
    从拜访记录中提取联系人信息
    
    Args:
        record: 拜访记录
        
    Returns:
        tuple: (contact_name, contact_position) 联系人姓名和职位
    """
    contact_name = None
    contact_position = None
    
    if isinstance(record, CompleteVisitRecordCreate):
        if record.contacts and len(record.contacts) > 0:
            # 多个联系人：格式化为 "姓名1（职位1）\n姓名2（职位2）" 格式
            contact_info_parts = []
            for contact in record.contacts:
                name = contact.name or ""
                position = contact.position or ""
                if name:
                    if position:
                        contact_info_parts.append(f"{name}（{position}）")
                    else:
                        contact_info_parts.append(name)
            if contact_info_parts:
                # 如果有多个联系人，用换行符分隔；单个联系人直接使用
                contact_name = "\n".join(contact_info_parts)
        else:
            # 兼容旧数据：使用单个联系人字段
            contact_name = record.contact_name
            contact_position = record.contact_position
    
    return contact_name, contact_position


def _build_visit_background_info(
    sales_name: Optional[str] = None,
    account_name: Optional[str] = None,
    contact_name: Optional[str] = None,
    contact_position: Optional[str] = None,
    visit_date: Optional[str] = None,
    opportunity_name: Optional[str] = None,
    is_first_visit: Optional[bool] = None,
    is_call_high: Optional[bool] = None,
    remarks: Optional[str] = None
) -> str:
    """
    构建拜访背景信息字符串
    
    Args:
        sales_name: 销售人员姓名
        account_name: 客户名称
        contact_name: 联系人姓名
        contact_position: 联系人职位
        visit_date: 拜访日期
        opportunity_name: 商机名称
        is_first_visit: 是否首次拜访
        is_call_high: 是否关键决策人拜访
        remarks: 现有风险或备注信息
        
    Returns:
        str: 背景信息字符串
    """
    if not any([sales_name, account_name, contact_name, contact_position, visit_date, opportunity_name, is_first_visit, is_call_high, remarks]):
        return ""
    
    background_info = "**背景信息（仅供理解，不在输出中显示）：**\n"
    if sales_name:
        background_info += f"• 销售人员：{sales_name}\n"
    if account_name:
        background_info += f"• 拜访客户：{account_name}\n"
    if contact_name:
        # 如果contact_name包含换行符，说明是多个联系人（格式：姓名1（职位1）\n姓名2（职位2））
        if "\n" in contact_name:
            background_info += f"• 拜访对象：\n"
            for contact_line in contact_name.split("\n"):
                if contact_line.strip():
                    background_info += f"  - {contact_line.strip()}\n"
        else:
            # 单个联系人
            contact_info = f"• 拜访对象：{contact_name}"
            if contact_position:
                contact_info += f"（{contact_position}）"
            background_info += contact_info + "\n"
    if visit_date:
        background_info += f"• 拜访日期：{visit_date}\n"
    if opportunity_name:
        background_info += f"• 商机名称：{opportunity_name}\n"
    if is_first_visit is not None:
        background_info += f"• 拜访类型：{'首次拜访' if is_first_visit else '多次拜访'}\n"
    if is_call_high is not None:
        background_info += f"• 拜访层级：{'关键决策人拜访' if is_call_high else '普通拜访'}\n"
    background_info += "• 文档类型：销售拜访记录会议文件\n"
    if remarks and remarks.strip():
        background_info += f"• 风险/备注：{remarks}\n"
    background_info += "\n"
    
    return background_info


def _parse_risk_extraction_json(raw: str) -> Optional[dict]:
    """解析风险抽取 JSON；兼容 markdown fence 与前后夹杂说明。"""
    text = (raw or "").strip()
    if not text:
        return None

    candidates = [text]
    fence_match = _RISK_INFO_FENCE_RE.match(text)
    if fence_match:
        fenced = (fence_match.group(1) or "").strip()
        if fenced:
            candidates.append(fenced)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1].strip())

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_risk_info(raw: Optional[str]) -> str:
    """
    解析约定 JSON {"evidences": [...], "risk": "..."}，返回卡片用纯文本。
    空证据 / 空 risk / 解析失败 → ""。
    """
    parsed = _parse_risk_extraction_json(raw or "")
    if not parsed:
        return ""

    raw_evidences = parsed.get("evidences")
    evidences = [
        str(item).strip()
        for item in (raw_evidences if isinstance(raw_evidences, list) else [])
        if str(item).strip()
    ]
    risk_text = parsed.get("risk")
    risk_text = risk_text.strip() if isinstance(risk_text, str) else ""

    # 显式 evidences=[] ⇒ 无风险；有证据但 risk 为空时用证据拼接兜底
    if "evidences" in parsed and not evidences:
        return ""
    if evidences and not risk_text:
        return "；".join(evidences)
    return risk_text


def extract_risk_info_from_content(
    content: str,
    title: Optional[str] = None,
    sales_name: Optional[str] = None,
    account_name: Optional[str] = None,
    contact_name: Optional[str] = None,
    contact_position: Optional[str] = None,
    visit_date: Optional[str] = None,
    opportunity_name: Optional[str] = None,
    is_first_visit: Optional[bool] = None,
    is_call_high: Optional[bool] = None,
    remarks: Optional[str] = None
) -> str:
    """
    从跟进记录正文中提取风险信息（一次 LLM 调用完成）。
    正文可能来自会议纪要/听记转写文档，也可能是销售手填的跟进内容、跟进记录或下一步计划。

    约定模型输出 JSON：{"evidences": [...], "risk": "..."}（先证据后结论）；
    本函数解析后返回 risk 纯文本（无风险为 ""），供 document_contents.risk_info
    落库及拜访卡片 risk_info 字段填充。
    
    Args:
        content: 待分析的跟进记录正文
        title: 内容来源说明或标题（可选）
        sales_name: 销售人员姓名（可选）
        account_name: 客户名称（可选）
        contact_name: 联系人姓名（可选）
        contact_position: 联系人职位（可选）
        visit_date: 拜访日期（可选）
        opportunity_name: 商机名称（可选）
        is_first_visit: 是否首次拜访（可选）
        is_call_high: 是否Call High（可选）
        remarks: 现有的remarks内容（作为上下文，可选）
        
    Returns:
        str: 提取的风险信息纯文本；无风险或解析失败时返回空字符串
    """
    if not content or not content.strip():
        return ""
    
    # 构建背景信息
    background_info = _build_visit_background_info(
        sales_name=sales_name,
        account_name=account_name,
        contact_name=contact_name,
        contact_position=contact_position,
        visit_date=visit_date,
        opportunity_name=opportunity_name,
        is_first_visit=is_first_visit,
        is_call_high=is_call_high,
        remarks=remarks
    )
    
    prompt = f"""{background_info}你是一位专业的销售风险分析专家，需要从销售跟进记录正文中提取风险信息，并严格输出 JSON。

**内容来源**：{title or "销售跟进记录（文档转写或手填内容）"}

**待分析内容**（可能为会议纪要/听记转写，或销售手填的跟进记录、跟进内容、下一步计划）：
{content}

**任务说明**：
仅从上述「待分析内容」中识别并提取与项目推进相关的风险信息。风险包括但不限于：客户担忧疑虑异议、技术难点实施风险、业务竞争压力、时间紧迫性或预算限制、决策障碍与不确定性、客户内部阻力或组织变化、项目延期或交付风险，以及其他可能影响成交或交付的风险因素。

**提取流程（必须严格按顺序）**：
1. 先填写 evidences：从正文摘录可核对的风险证据（原文连续子串或近原文短句，每条一条）；无风险则为 []
2. 再填写 risk：仅基于 evidences 汇总成一段自然语言；evidences 为空时 risk 必须为 ""
3. 不要推测、编造或补充正文未出现的信息；可参考背景信息理解上下文，但 evidences/risk 必须能在正文中找到依据
4. 忽略非风险信息：进展、成果、签约付款、合同归档、交付准备等正向描述不得写入 evidences/risk
5. 处理重复：同义风险只保留一条 evidence；risk 合并去重，控制在 150 字以内，不用列表

**禁止写入 evidences / risk 的内容**：
- 「未提及任何风险」「无风险信息」「相反……」「文档明确指出……无遗留问题」等说明性、否定性、meta 表述
- 对正文是否含风险的判断或总结
- 项目进展、合作顺利、已完成事项等非风险描述

【收敛规则】
- 先提取 evidences 再写 risk；二者不得矛盾。
- len(evidences) = 0 时，risk 必须为 ""。
- len(evidences) ≥ 1 时，risk 必须为基于 evidences 的自然语言摘要，不得为空，不得写入 evidences 未覆盖的信息。

【输出格式（仅 JSON）】
{{
  "evidences": ["从原文摘录的风险证据，无则为 []"],
  "risk": "基于 evidences 的一段自然语言摘要（不用列表，150字以内）；无风险时必须为空字符串"
}}

示例（有风险）：
{{
  "evidences": ["客户担心历史数据迁移失败", "要求先给回滚预案后再推进试点"],
  "risk": "客户担心历史数据迁移失败，要求先给回滚预案后再推进试点。"
}}

示例（无风险）：
{{
  "evidences": [],
  "risk": ""
}}

要求：
- 仅输出 JSON，不要任何前后缀、解释文字或 markdown。
- 必须包含 evidences 与 risk 字段；不要输出其他字段。
- evidences 必须先于 risk 填写；risk 须与 evidences 一致。
- 必须使用双引号（"），不能使用单引号。
- 不能有尾随逗号。
- 字符串中的引号必须正确转义。
- 输出必须能被标准 JSON 解析器直接解析。
- 无风险时必须输出 {{"evidences": [], "risk": ""}}：不要省略字段，不要用 null / NONE / 空代码块代替。
- 不得把「无风险」「未提及风险」等 meta 表述写入 evidences 或 risk。
"""
    
    try:
        result = call_ark_llm(
            prompt,
            temperature=0,
            # response_format={"type": "json_object"},
        )
        logger.info("Risk extraction result: %s", result)
        return _normalize_risk_info(result)
    except Exception as e:
        logger.warning(f"提取风险信息失败: {e}")
        return ""


def extract_visit_method_from_content(content: str, db_session: SessionDep) -> str:
    """
    从文本中抽取拜访及沟通方式（仅返回配置表允许的值）。
    """
    if not content:
        return ""
    try:
        from app.models.crm_system_configurations import CRMSystemConfiguration
        from sqlmodel import select

        stmt = select(CRMSystemConfiguration.config_key).where(
            CRMSystemConfiguration.config_type == "CommunicationMediumCategory",
            CRMSystemConfiguration.is_active == True,
        )
        method_candidates = [str(x).strip() for x in db_session.exec(stmt).all() if str(x).strip()]
        if not method_candidates:
            return ""

        options_text = "\n".join(f"- {m}" for m in method_candidates)
        prompt = f"""你是销售运营助手，需要从销售拜访记录内容中识别“跟进方式”。
你必须且只能从候选列表中选择一个值；如果无法判断，返回空字符串。

{options_text}

记录内容：
{content}

要求：
1. 输出必须为单行纯文本，且与候选项“完全一致”（逐字匹配，包含中英文大小写与空格）；
2. 不允许输出候选项之外的任何内容；
3. 不要输出解释、理由、标点、编号、前后缀、引号、换行或 markdown；
4. 如果无法判断，直接返回空字符串。

仅输出最终答案（一个候选值或空字符串）："""

        raw = (call_ark_llm(prompt, temperature=0) or "").strip()
        if not raw:
            return ""
        for method in method_candidates:
            if raw == method:
                return method
        for method in method_candidates:
            if method in raw:
                return method
        return ""
    except Exception as e:
        logger.warning(f"Failed to extract visit communication method: {e}")
        return ""


def _trigger_document_qa_extraction(document_content_id: int) -> None:
    try:
        from app.tasks.document_qa import extract_and_save_document_qa

        extract_and_save_document_qa.delay(document_content_id)
        logger.info(f"已异步触发文档问答对抽取任务，文档ID: {document_content_id}")
    except Exception as e:
        logger.error(f"触发文档问答对抽取异步任务失败: {e}")


def _apply_visit_record_document_llm_enrichment(
    document_content: Any,
    record: SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    content: str,
    db_session: SessionDep,
    title: Optional[str] = None,
) -> None:
    """对已落库的 document_content 并行执行风险抽取与会议纪要 LLM。"""
    document_content_repo = DocumentContentRepo()
    contact_name, contact_position = _extract_contact_info_from_record(record)
    llm_context = {
        "content": content,
        "title": title,
        "sales_name": record.recorder,
        "account_name": record.account_name,
        "contact_name": contact_name,
        "contact_position": contact_position,
        "visit_date": record.visit_communication_date,
        "opportunity_name": record.opportunity_name,
        "is_first_visit": record.is_first_visit,
        "is_call_high": record.is_call_high,
        "remarks": record.remarks,
    }

    def _run_risk_extraction() -> str:
        return extract_risk_info_from_content(**llm_context)

    def _run_meeting_summary() -> dict:
        from app.services.meeting_summary_service import MeetingSummaryService

        return MeetingSummaryService().generate_meeting_summary(**llm_context)

    risk_info: Optional[str] = None
    summary_result: Optional[dict] = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        risk_future = executor.submit(_run_risk_extraction)
        summary_future = executor.submit(_run_meeting_summary)
        try:
            risk_info = risk_future.result()
        except Exception as e:
            logger.error(f"并行提取风险信息时出错: {e}")
        try:
            summary_result = summary_future.result()
        except Exception as e:
            logger.error(f"并行生成会议纪要总结时出错: {e}")

    try:
        if risk_info:
            document_content_repo.update_risk_info(
                session=db_session,
                document_content_id=document_content.id,
                risk_info=risk_info,
                risk_status="success",
                auto_commit=False,
            )
            logger.info(f"成功提取并保存风险信息到document_contents，文档ID: {document_content.id}")
        else:
            document_content_repo.update_risk_info(
                session=db_session,
                document_content_id=document_content.id,
                risk_info="",
                risk_status="success" if risk_info is not None else "failed",
                auto_commit=False,
            )
            if risk_info is not None:
                logger.debug(f"未从文档内容中提取到风险信息，文档ID: {document_content.id}")
    except Exception as e:
        logger.error(f"保存风险信息时出错: {e}")
        try:
            document_content_repo.update_risk_info(
                session=db_session,
                document_content_id=document_content.id,
                risk_info="",
                risk_status="failed",
                auto_commit=False,
            )
        except Exception as update_error:
            logger.error(f"更新风险信息失败状态到数据库失败: {update_error}")

    try:
        if summary_result and summary_result.get("success"):
            meeting_summary = summary_result["summary"]
            try:
                document_content_repo.update_meeting_summary(
                    session=db_session,
                    document_content_id=document_content.id,
                    meeting_summary=meeting_summary,
                    summary_status="success",
                    auto_commit=False,
                )
                logger.info(f"成功生成并保存会议纪要总结，文档ID: {document_content.id}")
            except Exception as update_error:
                logger.error(f"保存会议纪要到数据库失败: {update_error}")
        else:
            error_msg = (summary_result or {}).get("error")
            try:
                document_content_repo.update_meeting_summary(
                    session=db_session,
                    document_content_id=document_content.id,
                    meeting_summary="",
                    summary_status="failed",
                    auto_commit=False,
                )
                logger.warning(
                    f"生成会议纪要总结失败，文档ID: {document_content.id}, 错误: {error_msg}"
                )
            except Exception as update_error:
                logger.error(f"更新会议纪要失败状态到数据库失败: {update_error}")
    except Exception as e:
        logger.error(f"生成会议纪要总结时出错: {e}")
        try:
            document_content_repo.update_meeting_summary(
                session=db_session,
                document_content_id=document_content.id,
                meeting_summary="",
                summary_status="failed",
                auto_commit=False,
            )
        except Exception as update_error:
            logger.error(f"更新会议纪要失败状态到数据库失败: {update_error}")


def create_visit_record_document_content(
    record_id: str,
    record: SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    content: str,
    document_type: str,
    user_id: UUID,
    db_session: SessionDep,
    title: Optional[str] = None,
) -> int:
    """为已落库拜访记录写入原始文档内容（不含 LLM enrichment）。"""
    document_content_repo = DocumentContentRepo()
    document_content = document_content_repo.create_document_content(
        session=db_session,
        raw_content=content,
        document_type=document_type,
        source_url=record.visit_url,
        user_id=user_id,
        visit_record_id=record_id,
        title=title,
        auto_commit=False,
    )
    return document_content.id


def enrich_existing_visit_record_document_content(
    record: SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    document_content_id: int,
    db_session: SessionDep,
) -> int:
    """对已写入 raw content 的拜访文档执行 LLM enrichment 并触发问答对任务。"""
    from app.models.document_contents import DocumentContent

    document_content = db_session.get(DocumentContent, document_content_id)
    if not document_content:
        raise ValueError(f"document_content not found: {document_content_id}")
    content = document_content.raw_content or ""
    title = document_content.title
    _apply_visit_record_document_llm_enrichment(
        document_content,
        record,
        content,
        db_session,
        title=title,
    )
    _trigger_document_qa_extraction(document_content_id)
    return document_content_id


def enrich_visit_record_with_document_content(
    record_id: str,
    record: SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    content: str,
    document_type: str,
    user_id: UUID,
    db_session: SessionDep,
    title: Optional[str] = None,
) -> int:
    """
    为已落库的拜访记录写入文档内容并执行 LLM enrichment（风险、纪要、问答对任务）。

    Returns:
        document_content.id
    """
    document_content_id = create_visit_record_document_content(
        record_id=record_id,
        record=record,
        content=content,
        document_type=document_type,
        user_id=user_id,
        db_session=db_session,
        title=title,
    )
    from app.models.document_contents import DocumentContent

    document_content = db_session.get(DocumentContent, document_content_id)
    _apply_visit_record_document_llm_enrichment(
        document_content,
        record,
        content,
        db_session,
        title=title,
    )
    _trigger_document_qa_extraction(document_content_id)
    return document_content_id


def save_visit_record_with_raw_content(
    record: SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    content: str,
    document_type: str,
    user: CurrentUserDep,
    db_session: SessionDep,
    title: Optional[str] = None,
) -> tuple[str, int]:
    """落库拜访记录并写入原始文档内容，LLM enrichment 由 Celery 异步完成。"""
    record_id, _saved_time = save_visit_record_to_crm_table(record, db_session)
    document_content_id = create_visit_record_document_content(
        record_id=record_id,
        record=record,
        content=content,
        document_type=document_type,
        user_id=user.id,
        db_session=db_session,
        title=title,
    )
    return record_id, document_content_id


def run_link_visit_enrichment_and_notify(
    record_id: str,
    record: SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    record_snapshot: dict,
    operator_user_id: UUID,
    db_session: SessionDep,
    *,
    content: Optional[str] = None,
    document_type: Optional[str] = None,
    title: Optional[str] = None,
    document_content_id: Optional[int] = None,
) -> int:
    """
    link 拜访 Celery 收尾：文档 LLM enrichment → 通知 Aldebaran 推卡。
    听记路径传入 content/document_type；其他 link 传入 document_content_id。
    """
    from app.models.document_contents import DocumentContent
    from app.services.visit_record_card_push_status import (
        VisitRecordCardPushStatus,
        get_visit_record_card_push_status,
    )

    document_content_repo = DocumentContentRepo()

    def _is_llm_enrichment_done(doc: DocumentContent) -> bool:
        risk_done = (doc.risk_extract_status or "") in {"success", "failed"}
        summary_done = (doc.summary_status or "") in {"success", "failed"}
        return risk_done and summary_done

    doc_id: int
    if document_content_id is not None:
        document_content = db_session.get(DocumentContent, document_content_id)
        if document_content and _is_llm_enrichment_done(document_content):
            doc_id = document_content_id
        else:
            doc_id = enrich_existing_visit_record_document_content(
                record=record,
                document_content_id=document_content_id,
                db_session=db_session,
            )
    else:
        if content is None or document_type is None:
            raise ValueError("content and document_type required when document_content_id is omitted")
        existing = document_content_repo.get_by_visit_record_id(db_session, record_id)
        if existing and _is_llm_enrichment_done(existing):
            doc_id = existing.id
        else:
            doc_id = enrich_visit_record_with_document_content(
                record_id=record_id,
                record=record,
                content=content,
                document_type=document_type,
                user_id=operator_user_id,
                db_session=db_session,
                title=title,
            )

    card_status = get_visit_record_card_push_status(db_session, record_id)
    if card_status in {
        VisitRecordCardPushStatus.PENDING,
        VisitRecordCardPushStatus.AWAITING_CALLBACK,
        VisitRecordCardPushStatus.PUSHED,
        VisitRecordCardPushStatus.PARTIAL_PUSHED,
    }:
        logger.info(
            "Skip duplicate Aldebaran notify, record_id=%s status=%s",
            record_id,
            card_status,
        )
        return doc_id

    notify_aldebaran_visit_record_saved(
        record_id=record_id,
        visit_snapshot=record_snapshot,
        db_session=db_session,
        operator_user_id=operator_user_id,
        visit_type=record.visit_type or "link",
    )
    return doc_id


def save_visit_record_with_content(
    record: SimpleVisitRecordCreate | CompleteVisitRecordCreate,
    content: str,
    document_type: str,
    user: CurrentUserDep,
    db_session: SessionDep,
    title: Optional[str] = None
) -> dict:
    """
    保存拜访记录和文档内容的公共函数
    
    Args:
        record: 拜访记录
        content: 文档内容
        document_type: 文档类型
        user: 当前用户
        db_session: 数据库会话
        title: 文档标题（可选）
        
    Returns:
        dict: 操作结果
        
    Raises:
        Exception: 当核心数据保存失败时抛出异常，由调用方处理事务回滚
    """
    record_id, _saved_time = save_visit_record_to_crm_table(record, db_session)
    enrich_visit_record_with_document_content(
        record_id=record_id,
        record=record,
        content=content,
        document_type=document_type,
        user_id=user.id,
        db_session=db_session,
        title=title,
    )
    return {
        "code": 0,
        "message": "success",
        "data": {
            "record_id": record_id,
        },
    }


def process_visit_record_content_reliable(followup_content: str = None, followup_record: str = None, next_steps: str = None) -> dict:
    """
    可靠的拜访记录内容处理函数
    将任务分组处理，在保证可靠性的同时减少LLM调用次数
    
    Args:
        followup_content: 跟进内容（简易版表单使用）
        followup_record: 跟进记录（完整版表单使用）
        next_steps: 下一步计划（完整版表单使用）
        
    Returns:
        dict: 包含所有处理结果的字典
    """
    if not followup_content and not followup_record:
        return {
            "followup_record": "",
            "followup_record_zh": "",
            "followup_record_en": "",
            "next_steps": "",
            "next_steps_zh": "",
            "next_steps_en": "",
            "followup_quality_level_zh": "不合格",
            "followup_quality_reason_zh": "内容为空",
            "followup_quality_level_en": "unqualified",
            "followup_quality_reason_en": "Content is empty",
            "next_steps_quality_level_zh": "不合格",
            "next_steps_quality_reason_zh": "内容为空",
            "next_steps_quality_level_en": "unqualified",
            "next_steps_quality_reason_en": "Content is empty"
        }
    
    try:
        # 第一步：内容拆分（仅简易版表单需要）
        if followup_content:
            followup_record, next_steps = extract_followup_record_and_next_steps(followup_content)
            logger.info(f"Extract from original followup content:\n[followup record]\n{followup_record}\n\n[next steps]\n{next_steps}")
        else:
            followup_record = followup_record or ""
            next_steps = next_steps or ""
        
        # 第二步：双语生成（按配置启用，默认关闭以缩短链路）
        if _should_generate_multilingual_content():
            bilingual_result = generate_bilingual_content_batch(followup_record, next_steps)
        else:
            bilingual_result = {
                "followup_record_zh": followup_record or "",
                "followup_record_en": followup_record or "",
                "next_steps_zh": next_steps or "",
                "next_steps_en": next_steps or ""
            }
        
        # 第三步：质量评估（批量处理）
        quality_result = assess_quality_batch(bilingual_result["followup_record_zh"], bilingual_result["followup_record_en"], 
                                            bilingual_result["next_steps_zh"], bilingual_result["next_steps_en"])
        
        return {
            "followup_record": followup_record,
            "followup_record_zh": bilingual_result["followup_record_zh"],
            "followup_record_en": bilingual_result["followup_record_en"],
            "next_steps": next_steps,
            "next_steps_zh": bilingual_result["next_steps_zh"],
            "next_steps_en": bilingual_result["next_steps_en"],
            "followup_quality_level_zh": quality_result["followup_quality_level_zh"],
            "followup_quality_reason_zh": quality_result["followup_quality_reason_zh"],
            "followup_quality_level_en": quality_result["followup_quality_level_en"],
            "followup_quality_reason_en": quality_result["followup_quality_reason_en"],
            "next_steps_quality_level_zh": quality_result["next_steps_quality_level_zh"],
            "next_steps_quality_reason_zh": quality_result["next_steps_quality_reason_zh"],
            "next_steps_quality_level_en": quality_result["next_steps_quality_level_en"],
            "next_steps_quality_reason_en": quality_result["next_steps_quality_reason_en"]
        }
        
    except Exception as e:
        logger.warning(f"Failed to process visit record content reliably: {e}")
        # 返回默认值
        return {
            "followup_record": followup_record or followup_content or "",
            "followup_record_zh": followup_record or followup_content or "",
            "followup_record_en": followup_record or followup_content or "",
            "next_steps": next_steps or "",
            "next_steps_zh": next_steps or "",
            "next_steps_en": next_steps or "",
            "followup_quality_level_zh": "不合格",
            "followup_quality_reason_zh": "AI处理失败，请重试",
            "followup_quality_level_en": "unqualified",
            "followup_quality_reason_en": "AI processing failed, please retry",
            "next_steps_quality_level_zh": "不合格",
            "next_steps_quality_reason_zh": "AI处理失败，请重试",
            "next_steps_quality_level_en": "unqualified",
            "next_steps_quality_reason_en": "AI processing failed, please retry"
        }


def generate_bilingual_content_batch(followup_record: str, next_steps: str) -> dict:
    """
    批量生成双语内容
    """
    if not followup_record and not next_steps:
        return {
            "followup_record_zh": "",
            "followup_record_en": "",
            "next_steps_zh": "",
            "next_steps_en": ""
        }
    
    prompt = f"""
你是一个专业的翻译专家，请将以下内容翻译成中文和英文版本。

**原始内容**：
跟进记录：{followup_record or ""}
下一步计划：{next_steps or ""}

请按照以下要求进行翻译：
1. 中文版本(zh)：主要使用中文表达，专业术语、品牌名称、产品名称等可以保持原文
2. 英文版本(en)：主要使用英文表达，专业术语、品牌名称、产品名称等可以保持原文
3. 保持专业性和准确性
4. 保持原文的意思和语气
5. 优先使用目标语言的表达习惯
6. **重要：不要添加"跟进记录："或"下一步计划："等前缀，只翻译内容本身**
7. **英文版本要求：在保持语意精准、不丢失信息的前提下，尽量使用精炼的表达，避免冗长句式**
**输出要求：**
1. 输出必须是纯JSON，不能包含任何前缀、后缀或解释性文字。
2. 必须使用双引号（"），不能使用单引号。
3. 不能有尾随逗号。
4. 字符串中的引号必须正确转义。
5. 输出必须能被标准JSON解析器直接解析。

**示例：**
{{
  "followup_record_zh": "向客户介绍了产品功能，客户对自动化处理很感兴趣，询问了价格和部署时间",
  "followup_record_en": "Introduced product features to client, who showed interest in automation capabilities and inquired about pricing and deployment timeline",
  "next_steps_zh": "下周三前发送详细报价，安排技术演示",
  "next_steps_en": "Send detailed quote by next Wednesday and schedule technical demo"
}}

**重要提示：**
- 如果原始内容为空，对应的翻译也为空字符串
- 优先使用目标语言的标点符号
- 专业术语、品牌名称、产品名称等可以保持原文
- **不要添加任何标签或前缀，只翻译内容本身**
- **英文翻译要求精炼：使用简洁句式，避免不必要的修饰词，保持信息完整性**
- 不要添加任何解释，只输出JSON
"""
    
    try:
        result = call_ark_llm(
            prompt,
            # response_format={"type": "json_object"},
        )
        data = json.loads(result)
        
        logger.info(f"Bilingual content result: {data}")
        
        return {
            "followup_record_zh": data.get("followup_record_zh", followup_record),
            "followup_record_en": data.get("followup_record_en", followup_record),
            "next_steps_zh": data.get("next_steps_zh", next_steps),
            "next_steps_en": data.get("next_steps_en", next_steps)
        }
    except Exception as e:
        logger.warning(f"Failed to generate bilingual content batch: {e}")
        # 失败时直接使用原文
        return {
            "followup_record_zh": followup_record or "",
            "followup_record_en": followup_record or "",
            "next_steps_zh": next_steps or "",
            "next_steps_en": next_steps or ""
        }


def _mirror_quality_level_zh_to_en(level_zh: str) -> str:
    """中文等级 → 英文等级（单源中文评估时与中文结论一致）。"""
    mapping = {
        "不合格": "unqualified",
        "合格": "qualified",
        "优秀": "excellent",
    }
    return mapping.get(level_zh, "unqualified")


def _fallback_followup_reason_en(level_zh: str) -> str:
    """模型未返回 reason_en 时的英文兜底（与等级一致）。"""
    return {
        "不合格": "Unqualified: template-like or vague content, or missing concrete actions and customer feedback.",
        "合格": "Qualified: concrete communication actions and locatable customer feedback are present.",
        "优秀": "Excellent: multiple concrete actions, specific customer feedback, and key details demonstrating sales professionalism and customer insight.",
    }.get(level_zh, "Unqualified: quality assessment could not be summarized in English.")


def _fallback_next_steps_reason_en(level_zh: str) -> str:
    """模型未返回 reason_en 时的英文兜底（与等级一致）。"""
    return {
        "不合格": "Unqualified: placeholder-like content, or missing concrete actions and time-bound plans.",
        "合格": "Qualified: concrete next actions, clear timing, and expected outcomes are stated.",
        "优秀": "Excellent: multiple executable actions, clear schedule, and explicit target outcomes.",
    }.get(level_zh, "Unqualified: quality assessment could not be summarized in English.")


def _coerce_followup_reason_en(raw: str | None, level_zh: str) -> str:
    t = (raw or "").strip()
    return t if t else _fallback_followup_reason_en(level_zh)


def _coerce_next_steps_reason_en(raw: str | None, level_zh: str) -> str:
    t = (raw or "").strip()
    return t if t else _fallback_next_steps_reason_en(level_zh)


def assess_quality_batch(followup_record_zh: str, followup_record_en: str, next_steps_zh: str, next_steps_en: str) -> dict:
    """
    批量进行质量评估，按内容类型分组处理。

    仅一次主评估：有中文则评中文，无中文则评英文；同一次输出中文结论与英文 reason（或仅英文路径下的中英 JSON）。
    level_en 由 level_zh 映射，与主结论一致；不再对英文字段单独发起第二路 LLM，避免与主结论语义冲突并节省调用。
    """
    # 检查跟进记录是否为空
    followup_empty = not followup_record_zh.strip() and not followup_record_en.strip()
    
    # 检查下一步计划是否为空
    next_steps_empty = not next_steps_zh.strip() and not next_steps_en.strip()
    
    def _evaluate_followup() -> dict:
        if followup_empty:
            return {
                "followup_quality_level_zh": "不合格",
                "followup_quality_reason_zh": "跟进记录内容为空，无法进行评估",
                "followup_quality_level_en": "unqualified",
                "followup_quality_reason_en": "Follow-up record is empty, cannot be assessed"
            }

        # 默认只用中文内容评估，避免中英混评带来的边界波动；
        # 若中文为空但英文存在，自动回退到英文评估，避免误判为空内容
        if followup_record_zh.strip():
            followup_result = assess_followup_quality_bilingual(followup_record_zh, "")
        else:
            followup_result = assess_followup_quality_bilingual("", followup_record_en)

        level_zh = followup_result.get("followup_quality_level_zh", "不合格")
        followup_result["followup_quality_level_en"] = _mirror_quality_level_zh_to_en(level_zh)
        return followup_result

    def _evaluate_next_steps() -> dict:
        if next_steps_empty:
            return {
                "next_steps_quality_level_zh": "不合格",
                "next_steps_quality_reason_zh": "下一步计划内容为空，无法进行评估",
                "next_steps_quality_level_en": "unqualified",
                "next_steps_quality_reason_en": "Next steps plan is empty, cannot be assessed"
            }

        # 默认只用中文内容评估，避免中英混评带来的边界波动；
        # 若中文为空但英文存在，自动回退到英文评估，避免误判为空内容
        if next_steps_zh.strip():
            next_steps_result = assess_next_steps_quality_bilingual(next_steps_zh, "")
        else:
            next_steps_result = assess_next_steps_quality_bilingual("", next_steps_en)

        level_zh = next_steps_result.get("next_steps_quality_level_zh", "不合格")
        next_steps_result["next_steps_quality_level_en"] = _mirror_quality_level_zh_to_en(level_zh)
        return next_steps_result

    # 并行执行两类评估，减少整体等待时间
    with ThreadPoolExecutor(max_workers=2) as executor:
        followup_future = executor.submit(_evaluate_followup)
        next_steps_future = executor.submit(_evaluate_next_steps)
        followup_result = followup_future.result()
        next_steps_result = next_steps_future.result()

    return {**followup_result, **next_steps_result}


def _followup_quality_prompt(followup_body: str) -> str:
    """跟进记录质量评估：同一套规则；正文可为中文或英文，仅嵌入处用中性标题【跟进记录】。"""
    return f"""
你是销售管理评审专家。请仅根据下方【跟进记录】评估质量，并严格输出 JSON。

【跟进记录】
{followup_body}

【说明】
- 仅评估上列正文；语言以正文为准（中文或英文均可）。不得因「另一语言字段未填写」判不合格。
- 跟进时间、对象、方式、参与人员等已在其他字段记录，本字段无需重复。
- 评估的是**信息含量与业务价值**，不是排版格式；编号/分节标题（如「1. 跟进内容」「2. 客户反馈」）本身不影响等级，只看能否提取有效事实。

【有效沟通动作的定义（计入 actions）】
- **算有效**：正文可逐字引用、且能明确说出「做了什么/讨论或演示了什么具体事项」的独立事实。
  例：「确认慢查询集中在报表模块」「演示索引优化与灰度步骤」「说明读写分离改造范围」。
- **不算有效**（不得计入 actions，也不得作为合格依据）：
  - 仅活动词无对象：已沟通/已讨论/已跟进/保持联系/discussed/had a call 等；
  - 仅笼统主题：讨论方案/介绍产品/沟通升级/introduced the solution 等，未点明具体议题或范围；
  - 纯过程套话：整体顺利/后续持续跟进/继续推进/will follow up 等；
  - 填写提示或占位：「（如：…）」「待补充」「TBD」「N/A」「test」「asdf」及同义占位；
  - 同一动作的同义重复（只计 1 条）。

【客户反馈的定义（计入 feedbacks）】
- **算具体反馈**：客户/对方可逐字引用的观点、要求、确认、异议、顾虑、条件、优先级等。
  例：「担心历史数据迁移风险」「要求周五前给回滚预案」「确认先覆盖订单查询链路」「可先在测试库验证」。
- **不算具体反馈**（不得计入 feedbacks）：
  - 笼统态度：认可/满意/有兴趣/OK/positive/interested 等，未说明认可什么或顾虑什么；
  - 销售单方转述且无客户原意：「客户整体认可方案」；
  - 无客户的空泛结论：整体顺利/暂无异议。

【评判流程（必须严格按顺序）】

步骤1) 占位/无效硬否决（仅下列情形直接判「不合格」，actions 与 feedbacks 均为空数组）：
  a. 全文为空、纯标点、纯问候，或乱码/无意义重复/与业务无关的测试文本；
  b. 正文**实质内容**仅为占位或填写提示（如整段「待补充」「TBD」、或「1. 跟进内容（如：…）2. 客户反馈（如：…）」且各项下无任何业务描述）；
  c. 除分节标题外，正文无法提取任何有效沟通动作。
  **注意**：若编号结构下各节已有真实业务描述，**不得**因「长得像模板」否决，必须进入步骤2。

步骤2) 证据提取（定级前必须完成；所有条目须为原文逐字摘录或连续子串，不得改写）：
  - actions：列出全部有效沟通动作（按上文定义），每条一条字符串；
  - feedbacks：列出全部具体客户反馈（按上文定义），每条一条字符串。
  分节标题、序号、「跟进内容/客户反馈/结论」等标签本身不得写入 actions 或 feedbacks。

步骤3) 按提取结果定级（仅依据 actions/feedbacks 数量与质量，不得脑补未出现的信息）：
  - **不合格**（任一即成立）：
    · len(actions) = 0；或
    · len(actions) ≥ 1 但 len(feedbacks) = 0，且全文无任何可计的具体客户反馈；或
    · 仅有空泛套话（如「已沟通、客户认可、后续跟进」类，提取后 actions 与 feedbacks 皆不足以达标）。
  - **合格**（须同时满足）：
    · len(actions) ≥ 1；
    · len(feedbacks) ≥ 1；
    · 不要求异议或负面反馈；客户确认/要求/顾虑均可。
  - **优秀**（须同时满足；任一条仅勉强沾边则最高判「合格」）：
    · len(actions) ≥ 2，且两条可区分（不同议题/动作，不得同义重复）；
    · len(feedbacks) ≥ 1，且至少 1 条为具体观点/要求/顾虑（非笼统认可/满意）；
    · 正文含可定位的业务细节（如具体问题、范围、方案要点、时间节点、风险点等），能体现推进价值或客户洞察。

【收敛规则】
- 先提取 evidence 再定 level；level 必须与 actions/feedbacks 统计结果一致，不得矛盾。
- **合格 vs 不合格**边界不清时：若 len(actions) ≥ 1 且 len(feedbacks) ≥ 1，判「合格」；若无法提取任一有效 action，判「不合格」。
- **合格 vs 优秀**边界不清、或 actions/feedbacks 刚达下限且信息密度一般时，统一判「合格」。
- reason/reason_en 须点明关键证据或缺失项（如「缺具体客户反馈」「含2项具体动作与迁移顾虑」），不得写与 level 矛盾的表述。
- 不得因未写异议而判不合格；不得因仅有编号格式而判不合格或降低等级。

【输出格式（仅 JSON）】
{{
  "followup_quality_zh": {{
    "actions": ["从原文摘录的有效沟通动作，无则为 []"],
    "feedbacks": ["从原文摘录的具体客户反馈，无则为 []"],
    "level": "不合格|合格|优秀",
    "reason": "不超过50字，使用中文表述关键问题或亮点（若正文为英文，用中文概括对英文内容的评判）",
    "reason_en": "Concise English (one or two sentences), same judgment as level/reason; normal assessment wording, no placeholder phrasing"
  }}
}}

要求：
- 仅输出 JSON，不要任何前后缀、解释文字或 markdown。
- actions、feedbacks 必须先于 level 填写；level 与 reason、reason_en 须与提取结果一致。
- reason_en 为地道英文，禁止仅写「与中文一致」类占位。
- 使用双引号；可被标准 JSON 解析。
"""


def _next_steps_quality_prompt(next_steps_body: str) -> str:
    """下一步计划质量评估：同一套规则；正文可为中文或英文，嵌入处用中性标题【下一步计划】。"""
    return f"""
你是销售管理评审专家。请仅根据下方【下一步计划】评估质量，并严格输出 JSON。

【下一步计划】
{next_steps_body}

【说明】
- 仅评估上列正文；语言以正文为准（中文或英文均可）。不得因「另一语言字段未填写」判不合格。
- 本次仅评估“下一步计划”字段；客户/项目/联系人等已在其他字段记录。
- 评估的是**计划的可执行性与推进价值**，不是排版格式；编号/分节标题（如「1. 待办事项」「2. 时间节点」）本身不影响等级，只看能否提取有效动作、时间与目标。

【明确时间的定义（计入 time_points；合格从宽）】
- 合格门槛：正文可逐字引用到**可定位的时间范围**即视为有效时间点，**不要求**精确到具体日期或星期几。
- **算有效时间点**（任一类即可）：相对周期（今日/明天/本周/下周/本月底/下月初/月底前；today/tomorrow/this week/next week/end of month 等）；相对星期（本周五/下周三/by Friday/next Monday 等）；具体日期/截止时点（如「本周四前」「by Wednesday」）。
- **典型合格**：「下周发送方案」「next week send proposal」——仅出现「下周/next week」等周级表述即满足时间安排，**不得**以「未写到哪一天」判不合格。
- **不算有效时间点**：全文无任何时间词或时间范围；仅有「安排会议/尽快/后续/待定/ASAP/follow up later」等而无何时、何周、何月完成。

【有效动作的定义（计入 actions）】
- **算有效**：正文可逐字引用、可独立执行的具体动作（发送/提交/安排/演示/评审/联调/报价等 + 明确对象或事项）。
  例：「发送 PoC 方案」「安排技术评审」「提交 PoC 清单」「完成试点环境联调」。
- **不算有效**（不得计入 actions）：
  - 空泛维持：保持沟通/等待反馈/持续跟进/stay in touch/waiting for feedback/后续跟进 等；
  - 仅活动无事项：安排会议/进一步沟通/对齐/同步 等，未说明会议或动作的具体目的与内容；
  - 占位或提示：「（如：…）」「待补充」「TBD」「N/A」「test」「asdf」及同义占位；
  - 同一动作同义重复（只计 1 条）。

【推进目标/结果的定义（计入 outcomes）】
- **算有效**：具体且完成后可判断是否达成的目标结果。
  例：「确认一期范围/测试范围」「推动客户内部立项」「锁定一期范围」「获取客户反馈」「签署合同」。
- **不算有效**（不得计入 outcomes）：「沟通」「讨论」「对齐」「同步」等活动性描述——它们是动作而非成果。

【评判流程（必须严格按顺序）】

步骤1) 占位/无效硬否决（仅下列情形直接判「不合格」，actions/time_points/outcomes 均为空数组，closed_case 为 false）：
  a. 全文为空、纯标点、纯问候，或乱码/无意义重复/与业务无关的测试文本；
  b. 正文**实质内容**仅为占位（如整段「TBD」「待补充」、或「1. 待办事项（如：…）2. 时间节点（如：…）」且各项下无任何计划描述）；
  c. 除分节标题外，无法提取任何有效动作。
  **注意**：若编号结构下各节已有真实计划内容，**不得**因「长得像模板」否决，必须进入步骤2。

步骤2) 特殊情形（优先于常规定级）：若正文明确商机关闭、无预算、无机会、客户无需求、仅保持触达，或 opportunity closed / no budget / no demand / touch base only 等同义表述，设 closed_case 为 true，直接判「合格」（actions/time_points/outcomes 可为空数组；reason 须说明属商机关闭/无后续推进类情形）。

步骤3) 证据提取（closed_case 为 false 时定级前必须完成；所有条目须为原文逐字摘录或连续子串，不得改写）：
  - actions：全部有效动作；
  - time_points：全部有效时间点（按「明确时间」定义）；
  - outcomes：全部有效推进目标/结果。
  分节标题、序号、「待办事项/时间节点/预期成果」等标签本身不得写入上述数组。
  动作与时间点可同句出现，分别计入对应数组（如「下周发送方案」→ action + time_point 各 1 条）。

步骤4) 按提取结果定级（closed_case 为 true 时跳过；仅依据 actions/time_points/outcomes，不得脑补）：
  - **不合格**（任一即成立）：
    · len(actions) = 0；或
    · len(time_points) = 0；或
    · len(outcomes) = 0；或
    · 仅有空泛维持类表述（如「保持沟通，等待客户反馈」「下周持续跟进客户」——有时间但无具体可执行动作或无有效 outcome）。
  - **合格**（须同时满足）：
    · len(actions) ≥ 1；
    · len(time_points) ≥ 1；
    · len(outcomes) ≥ 1。
  - **优秀**（须同时满足；任一条仅勉强沾边则最高判「合格」）：
    · len(actions) ≥ 2，且两条可区分（不同动作，不得同义重复）；
    · len(time_points) ≥ 2（可为两个不同日期/星期，或两个不同周/月范围；若仅 1 个周级时间且其余条件勉强，最高判合格）；
    · len(outcomes) ≥ 1，且至少 1 条为具体可验证结果（非活动性描述）；
    · 计划整体体现前瞻性与主动性（动作有递进关系或明确推进节奏）。

【收敛规则】
- 先提取 evidence 再定 level；level 必须与 actions/time_points/outcomes 及 closed_case 一致，不得矛盾。
- **合格 vs 不合格**：缺动作、缺时间或缺推进结果等硬条件，必须判「不合格」；**不得**因时间仅为周/月粒度（如仅写「下周」）而判不合格。
- **合格 vs 优秀**边界不清、或刚达下限且计划覆盖范围有限时，统一判「合格」。
- 时间从宽（合格）：含「下周/本周/下月/this week/next week」等即视为有时间；reason 不得写「缺少明确时间」「仅提到下周不够具体」等而将本应合格的内容判为不合格。
- reason/reason_en 须点明关键证据或缺失项（如「缺明确时间」「含具体动作与推进目标」），不得写与 level 矛盾的表述。
- 不得因仅有编号格式而判不合格或降低等级；「暂无/待定/TBD」单独成段视为占位不合格，但在商机关闭完整句中可配合 closed_case 判合格。

【输出格式（仅 JSON）】
{{
  "next_steps_quality_zh": {{
    "closed_case": false,
    "actions": ["从原文摘录的有效动作，无则为 []"],
    "time_points": ["从原文摘录的有效时间点，无则为 []"],
    "outcomes": ["从原文摘录的有效推进目标/结果，无则为 []"],
    "level": "不合格|合格|优秀",
    "reason": "不超过50字，使用中文表述关键问题或亮点（若正文为英文，用中文概括对英文内容的评判）",
    "reason_en": "Concise English (one or two sentences), same judgment as level/reason; normal assessment wording"
  }}
}}

要求：
- 仅输出 JSON，不要任何前后缀、解释文字或 markdown。
- closed_case、actions、time_points、outcomes 必须先于 level 填写；level 与 reason、reason_en 须与提取结果一致。
- 若 reason 写明缺少时间安排/无明确时间/缺具体动作/缺推进结果，level 必须为「不合格」（closed_case 为 true 时除外）。
- 若目标仅为过程性「沟通/讨论/同步」，level 不得为「优秀」。
- reason_en 为地道英文，禁止仅写「与中文一致」类占位。
- 使用双引号；可被标准 JSON 解析。
"""


def assess_followup_quality_bilingual(followup_record_zh: str, followup_record_en: str) -> dict:
    """
    评估跟进记录质量。有中文则评中文正文，否则评英文正文；同一套提示词（中性【跟进记录】）与解析逻辑。
    """
    # 检查内容是否为空
    if not followup_record_zh.strip() and not followup_record_en.strip():
        return {
            "followup_quality_level_zh": "不合格",
            "followup_quality_reason_zh": "跟进记录内容为空，无法进行评估",
            "followup_quality_level_en": "unqualified",
            "followup_quality_reason_en": "Follow-up record is empty, cannot be assessed"
        }

    # 有中文则评中文正文，否则评英文正文；同一套提示词与解析逻辑
    body = followup_record_zh.strip() if followup_record_zh.strip() else followup_record_en.strip()
    prompt = _followup_quality_prompt(body)
    try:
        result = call_ark_llm(
            prompt,
            temperature=0,
            # response_format={"type": "json_object"},
        )
        logger.info(f"Followup quality result: {result}")
        data = _safe_parse_json_object(result)
        zh = data.get("followup_quality_zh") or {}
        level_zh = zh.get("level", "不合格")
        reason_zh = zh.get("reason", "AI输出格式异常")
        reason_en = _coerce_followup_reason_en(zh.get("reason_en"), level_zh)
        return {
            "followup_quality_level_zh": level_zh,
            "followup_quality_reason_zh": reason_zh,
            "followup_quality_level_en": _mirror_quality_level_zh_to_en(level_zh),
            "followup_quality_reason_en": reason_en,
        }
    except Exception as e:
        logger.warning(f"Failed to assess followup quality bilingual: {e}")
        err_zh = "AI评估失败，请重试"
        return {
            "followup_quality_level_zh": "不合格",
            "followup_quality_reason_zh": err_zh,
            "followup_quality_level_en": _mirror_quality_level_zh_to_en("不合格"),
            "followup_quality_reason_en": "AI assessment failed. Please retry.",
        }


def assess_next_steps_quality_bilingual(next_steps_zh: str, next_steps_en: str) -> dict:
    """
    评估下一步计划质量。有中文则评中文正文，否则评英文正文；同一套提示词与解析逻辑。
    """
    # 检查内容是否为空
    if not next_steps_zh.strip() and not next_steps_en.strip():
        return {
            "next_steps_quality_level_zh": "不合格",
            "next_steps_quality_reason_zh": "下一步计划内容为空，无法进行评估",
            "next_steps_quality_level_en": "unqualified",
            "next_steps_quality_reason_en": "Next steps plan is empty, cannot be assessed"
        }

    body = next_steps_zh.strip() if next_steps_zh.strip() else next_steps_en.strip()
    prompt = _next_steps_quality_prompt(body)
    try:
        result = call_ark_llm(
            prompt,
            temperature=0,
            # response_format={"type": "json_object"},
        )
        logger.info(f"Next steps quality result: {result}")
        data = _safe_parse_json_object(result)
        zh = data.get("next_steps_quality_zh") or {}
        level_zh = zh.get("level", "不合格")
        reason_zh = zh.get("reason", "AI输出格式异常")
        reason_en = _coerce_next_steps_reason_en(zh.get("reason_en"), level_zh)
        return {
            "next_steps_quality_level_zh": level_zh,
            "next_steps_quality_reason_zh": reason_zh,
            "next_steps_quality_level_en": _mirror_quality_level_zh_to_en(level_zh),
            "next_steps_quality_reason_en": reason_en,
        }
    except Exception as e:
        logger.warning(f"Failed to assess next steps quality bilingual: {e}")
        err_zh = "AI评估失败，请重试"
        return {
            "next_steps_quality_level_zh": "不合格",
            "next_steps_quality_reason_zh": err_zh,
            "next_steps_quality_level_en": _mirror_quality_level_zh_to_en("不合格"),
            "next_steps_quality_reason_en": "AI assessment failed. Please retry.",
        }