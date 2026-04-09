import logging
import json
import requests
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Any
from datetime import datetime
from uuid import UUID
from app.core.config import settings
from app.core.db import get_db_session
from app.api.routes.crm.models import SimpleVisitRecordCreate, CompleteVisitRecordCreate
from app.api.deps import CurrentUserDep, SessionDep
from app.repositories.document_content import DocumentContentRepo
from app.services.platform_notification_service import platform_notification_service
from app.tasks.document_qa import extract_and_save_document_qa
from app.utils.ark_llm import call_ark_llm
from app.utils.uuid6 import uuid6
logger = logging.getLogger(__name__)

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
            response_format={"type": "json_object"},
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


def fill_sales_visit_record_fields(sales_visit_record, db_session):
    # 处理客户名称和合作伙伴
    account_name = sales_visit_record.get("account_name")
    partner_name = sales_visit_record.get("partner_name")
    if not account_name and partner_name:
        sales_visit_record["account_name"] = partner_name
    
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

    def _safe_strip(v) -> str:
        """将可能为 None/非字符串 的值安全转成字符串并 strip，避免 AttributeError。"""
        if v is None:
            return ""
        if isinstance(v, str):
            return v.strip()
        return str(v).strip()
    
    if contacts:
        # 如果提供了contacts字段（列表格式）
        if isinstance(contacts, list):
            for contact in contacts:
                if isinstance(contact, dict):
                    name = _safe_strip(contact.get("name"))
                    position = _safe_strip(contact.get("position"))
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
        contact_name = _safe_strip(sales_visit_record.get("contact_name"))
        contact_position = _safe_strip(sales_visit_record.get("contact_position"))
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
    for k, v in sales_visit_record.items():
        if v is None and k not in ["is_first_visit", "is_first_visit_en", "is_call_high", "is_call_high_en", "subject", "subject_en", "visit_start_time", "visit_end_time", "record_type", "visit_purpose"]:
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


def push_visit_record_message(record_id: str, sales_visit_record, visit_type, db_session=None, meeting_notes=None, risk_info=None, saved_time=None):
    try:
        # 如果没有传入db_session，则创建一个新的
        should_close_session = False
        if db_session is None:
            db_session = get_db_session()
            should_close_session = True
        
        sales_visit_record = fill_sales_visit_record_fields(sales_visit_record, db_session)
        # 补充记录人部门快照信息（从已落库的拜访记录中读取），供后续部门群匹配使用
        try:
            from sqlmodel import select
            from app.models.crm_sales_visit_records import CRMSalesVisitRecord

            stmt = select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
            db_row = db_session.exec(stmt).first()
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
            return False
        
        # 确保会议纪要不为空
        if meeting_notes is None or meeting_notes == "":
            meeting_notes = "--"
        # 如果配置了自定义字体大小token，则添加到会议纪要中，主要用于钉钉卡片中设置字体大小
        if settings.CUSTOM_FONT_SIZE_TOKEN:
            meeting_notes =f"<font sizeToken={settings.CUSTOM_FONT_SIZE_TOKEN}>{meeting_notes}</font>"

        # 发送拜访记录通知
        result = platform_notification_service.send_visit_record_notification(
            db_session=db_session,
            record_id=record_id,
            recorder_name=recorder_name,
            recorder_id=recorder_id,
            visit_record=sales_visit_record,
            visit_type=visit_type,
            meeting_notes=meeting_notes,
            risk_info=risk_info
        )
        
        if result["success"]:
            logger.info(f"Successfully pushed visit record notification: {result['message']}")
        else:
            logger.warning(f"Failed to push visit record notification: {result['message']}")
        
        return result["success"]
        
    except Exception as e:
        logger.error(f"发送拜访记录通知失败: {e}")
        return False
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
    从文档内容中提取风险信息（一次LLM调用完成）
    
    Args:
        content: 文档内容
        title: 文档标题（可选）
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
        str: 提取的风险信息，如果没有风险信息则返回空字符串
    """
    # 如果文档内容为空，直接返回空字符串
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
    
    prompt = f"""{background_info}你是一位专业的销售风险分析专家，需要从销售拜访文档中提取风险信息。

**文档标题**：{title or "未提供标题"}

**文档内容**：
{content}

**任务说明**：
从上述文档内容中识别并提取所有与风险相关的信息。风险信息包括但不限于：客户担忧疑虑异议、技术难点实施风险、业务风险竞争压力、时间紧迫性预算限制、决策障碍不确定性、客户内部阻力组织变化风险、项目延期风险交付风险，以及其他可能影响项目推进的风险因素。

**提取要求**：
1. **只从文档内容中提取**：仅提取文档正文中明确提及的风险信息，不要推测或编造
2. **参考背景信息**：结合背景信息（如拜访类型、拜访层级、现有风险/备注等）来更好地理解风险信息的上下文，但只提取文档中的风险信息
3. **具体明确**：提取的风险信息应该具体、明确，避免泛泛而谈
5. **处理重复**：如果文档中的风险信息与背景信息中的"风险/备注"重复，只提取文档中的信息（以文档为准）
6. **无风险信息**：如果文档内容中完全没有风险信息，直接返回空字符串

**输出要求（重要）**：
- 使用一段自然语言来描述，不要使用列表、要点或分条格式
- 字数严格控制在150字以内
- 使用简洁、专业的表达，避免冗余
- 直接输出提取的风险信息，不要添加任何前缀、后缀或说明文字
- 如果没有风险信息，直接返回空字符串

请输出提取的风险信息：
"""
    
    try:
        result = call_ark_llm(prompt, temperature=0)
        risk_info = result.strip()
        
        # 如果结果为空或只包含无意义的字符，返回空字符串
        if not risk_info or len(risk_info) < 5:
            return ""
        
        return risk_info
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
    # ========== 第一阶段：核心数据库事务操作 ==========
    # 先保存拜访记录以获取 record_id
    record_id, saved_time = save_visit_record_to_crm_table(record, db_session)
    
    # 保存文档内容
    document_content_repo = DocumentContentRepo()
    document_content = document_content_repo.create_document_content(
        session=db_session,
        raw_content=content,
        document_type=document_type,
        source_url=record.visit_url,
        user_id=user.id,
        visit_record_id=record_id,
        title=title,
        auto_commit=False
    )
    
    # 注意：不在这里commit，由调用方控制事务
    # db_session.commit()
    
    # ========== 第二阶段：提取风险信息并保存到document_contents表（不影响主流程） ==========
    try:
        # 提取联系人信息（使用公共函数）
        contact_name, contact_position = _extract_contact_info_from_record(record)
        
        # 提取风险信息（使用完整的背景信息和remarks作为上下文，但不修改remarks）
        risk_info = extract_risk_info_from_content(
            content=content,
            title=title,
            sales_name=record.recorder,
            account_name=record.account_name,
            contact_name=contact_name,
            contact_position=contact_position,
            visit_date=record.visit_communication_date,
            opportunity_name=record.opportunity_name,
            is_first_visit=record.is_first_visit,
            is_call_high=record.is_call_high,
            remarks=record.remarks
        )
        
        if risk_info:
            # 保存风险信息到document_contents表
            document_content_repo.update_risk_info(
                session=db_session,
                document_content_id=document_content.id,
                risk_info=risk_info,
                risk_status="success",
                auto_commit=False  # 不立即提交，等待主事务提交
            )
            logger.info(f"成功提取并保存风险信息到document_contents，文档ID: {document_content.id}")
        else:
            # 记录未提取到风险信息的状态
            document_content_repo.update_risk_info(
                session=db_session,
                document_content_id=document_content.id,
                risk_info="",
                risk_status="success",  # 虽然没有风险信息，但提取过程成功
                auto_commit=False
            )
            logger.debug(f"未从文档内容中提取到风险信息，文档ID: {document_content.id}")
    except Exception as e:
        logger.error(f"提取或保存风险信息时出错: {e}")
        # 记录失败状态（如果失败不影响主流程）
        try:
            document_content_repo.update_risk_info(
                session=db_session,
                document_content_id=document_content.id,
                risk_info="",
                risk_status="failed",
                auto_commit=False
            )
        except Exception as update_error:
            logger.error(f"更新风险信息失败状态到数据库失败: {update_error}")
        # 不影响主流程，继续执行
    
    # ========== 第三阶段：生成会议纪要总结（不影响主流程） ==========
    meeting_summary = None
    
    try:
        from app.services.meeting_summary_service import MeetingSummaryService
        meeting_summary_service = MeetingSummaryService()
        
        # 抽取联系人信息
        contact_name, contact_position = _extract_contact_info_from_record(record)
        
        summary_result = meeting_summary_service.generate_meeting_summary(
            content=content,
            title=title,
            sales_name=record.recorder,
            account_name=record.account_name,
            contact_name=contact_name,
            contact_position=contact_position,
            visit_date=record.visit_communication_date,
            opportunity_name=record.opportunity_name,
            is_first_visit=record.is_first_visit,
            is_call_high=record.is_call_high,
            remarks=record.remarks
        )
        
        if summary_result["success"]:
            meeting_summary = summary_result["summary"]
            
            # 更新会议纪要到数据库（如果失败不影响主流程）
            try:
                document_content_repo.update_meeting_summary(
                    session=db_session,
                    document_content_id=document_content.id,
                    meeting_summary=meeting_summary,
                    summary_status="success",
                    auto_commit=False  # 不立即提交，等待主事务提交
                )
                logger.info(f"成功生成并保存会议纪要总结，文档ID: {document_content.id}")
            except Exception as update_error:
                logger.error(f"保存会议纪要到数据库失败: {update_error}")
                # 不影响主流程，继续执行
        else:
            # 记录失败状态（如果失败不影响主流程）
            try:
                document_content_repo.update_meeting_summary(
                    session=db_session,
                    document_content_id=document_content.id,
                    meeting_summary="",
                    summary_status="failed",
                    auto_commit=False  # 不立即提交，等待主事务提交
                )
                logger.warning(f"生成会议纪要总结失败，文档ID: {document_content.id}, 错误: {summary_result.get('error')}")
            except Exception as update_error:
                logger.error(f"更新会议纪要失败状态到数据库失败: {update_error}")
                # 不影响主流程，继续执行
                
    except Exception as e:
        logger.error(f"生成会议纪要总结时出错: {e}")
        # 记录失败状态（如果失败不影响主流程）
        try:
            document_content_repo.update_meeting_summary(
                session=db_session,
                document_content_id=document_content.id,
                meeting_summary="",
                summary_status="failed",
                auto_commit=False
            )
        except Exception as update_error:
            logger.error(f"更新会议纪要失败状态到数据库失败: {update_error}")
        # 不影响主流程，继续执行
    
    # ========== 第四阶段：异步触发文档问答对抽取任务（不影响主流程） ==========
    try:
        extract_and_save_document_qa.delay(document_content.id)
        logger.info(f"已异步触发文档问答对抽取任务，文档ID: {document_content.id}")
    except Exception as e:
        logger.error(f"触发文档问答对抽取异步任务失败: {e}")
        # 不影响主流程，继续执行
    
    # ========== 第五阶段：推送飞书消息（不影响事务） ==========
    try:
        record_data = record.model_dump()
        # 推送飞书消息（保留附件字段，附件中仅包含URL和少量结构化信息，避免大体积base64）
        push_visit_record_message(
            record_id=record_id,
            sales_visit_record=record_data,
            visit_type=record.visit_type,
            db_session=db_session,
            meeting_notes=meeting_summary,
            risk_info=risk_info,
            saved_time=saved_time
        )
    except Exception as e:
        logger.error(f"推送飞书消息失败: {e}")
        # 不影响主流程，继续执行
    
    return {"code": 0, "message": "success", "data": {}}


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
            response_format={"type": "json_object"},
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
- 重点评估：沟通过程是否具体、客户反馈是否具体、是否体现推进价值。

【评判流程（必须严格按顺序，禁止跳步）】

步骤0) 模板特征扫描（必须首先执行）：
  逐条检查正文是否命中以下任一模板/占位符模式——命中任意一条即直接判“不合格”，不得进入后续步骤：
  a. 含「（如：…）」「（例如：…）」「（请填写…）」「如：…」等填写提示括号；
  b. 整体为编号骨架 + 提示语结构，如「1. 跟进内容 2. 客户反馈 3. 结论」且各项仅有标题/提示、无实质业务描述；
  c. 含「请输入」「待补充」「TBD」「N/A」「test」「asdf」等占位/测试标记；
  d. 全文为乱码、无意义字符重复、或与业务完全无关的测试文本。
  关键：模板提示中的示例文字（如“沟通事项”“观点/异议”）不是真实业务内容，不得作为评估依据。

步骤1) 硬否决复核：若步骤0未命中，再检查是否存在其他无意义内容（纯问候、纯标点等），是则判“不合格”。

步骤2) 常规质量评估（仅在步骤0和步骤1均通过后执行）：
   - 不合格：过程过于模糊、仅“已沟通/已讨论”等空泛表述、无有效客户反馈、无实际业务信息。
   - 合格：非模板；过程描述清晰具体且至少包含1个明确沟通动作/事实；有可定位的客户反馈或观点；体现对客户需求理解；表达清楚。
   - 优秀：在合格基础上，内容具体详实，且同时满足：
     a. 至少2个具体沟通动作/事实（如具体讨论了什么、演示了什么、确认了什么）；
     b. 至少1条具体的客户反馈（客户的具体观点、具体建议、具体异议或担忧，而非笼统的“认可”“满意”）；
     c. 内容包含关键细节，能体现销售专业性或客户洞察（如具体问题定位、针对性方案、客户的具体需求/顾虑等）。

【收敛规则】
- 证据锚定：每个判断都必须能在原文找到直接证据（可逐字引用），不能脑补或推断。模板提示中的示例词不算证据。
- 冲突裁决：若“合格/优秀”边界不清或证据不足，统一判“合格”。
- 不因“未写异议”而直接不合格；但若内容空泛，也不得判优秀。
- 编号/要点列表若承载真实业务事实，视为真实记录，不判模板。
- 严格从严：证据刚好达标且信息密度一般时，优先判“合格”而非“优秀”。

【输出格式（仅 JSON）】
{{
  "followup_quality_zh": {{
    "level": "不合格|合格|优秀",
    "reason": "不超过50字，使用中文表述关键问题或亮点（若正文为英文，用中文概括对英文内容的评判）",
    "reason_en": "Concise English (one or two sentences), same judgment as level/reason; normal assessment wording, no placeholder phrasing"
  }}
}}

要求：
- 仅输出 JSON，不要任何前后缀、解释文字或 markdown。
- reason 与 reason_en 须与 level 一致；reason_en 为地道英文，禁止仅写「与中文一致」类占位。
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
- 重点评估：计划是否具体、可执行、时间是否明确、是否有推进目标。

【明确时间的定义（常规推进必查）】
- 须在正文可逐字引用：具体日期、星期、或相对时间词。中文例如：今日/明天/本周X/下周/本月底/周五前等；英文例如：today/tomorrow/this week/next Friday/end of month/by Friday 等。
- 下列情形一律视为「无明确时间安排」，不得判合格或优秀：全文无可定位时间点；仅有「安排会议/尽快/后续」或 schedule a meeting / ASAP / follow up 等而无何时完成。

【评判流程（必须严格按顺序，禁止跳步）】

步骤0) 模板特征扫描（必须首先执行）：
  逐条检查正文是否命中以下任一模板/占位符模式——命中任意一条即直接判“不合格”，不得进入后续步骤：
  a. 含「（如：…）」「（例如：…）」「（请填写…）」「如：…」等填写提示括号；
  b. 整体为编号骨架 + 提示语结构，如「1. 待办事项 2. 时间节点 3. 预期成果」且各项仅有标题/提示、无实质业务描述；
  c. 含「请输入」「待补充」「TBD」「N/A」「test」「asdf」等占位/测试标记；
  d. 全文为乱码、无意义字符重复、或与业务完全无关的测试文本。
  关键：模板提示中的示例文字（如“具体动作”“完成时间”）不是真实业务内容，不得作为评估依据。

步骤1) 特殊情形（优先于常规判定）：若正文明确商机关闭、无预算、无机会、客户无需求、仅保持触达，或英文 opportunity closed / no budget / no demand / touch base only 等同义表述，判“合格”（可无具体动作、无时间安排）。

步骤2) 硬否决复核：若步骤0和步骤1均未命中，再检查是否存在其他无意义内容（纯问候、纯标点等），是则判“不合格”。

步骤3) 常规推进判定（须同时满足下列各条才可判合格；任一条不满足则判不合格，不得以「动作很具体」放宽）：
   - 不合格：仅保持沟通/等待反馈/持续跟进或 stay in touch / waiting for feedback 等空泛内容；或无具体动作；或按上文定义无明确时间安排；或未写明预期推进方向/结果。
   - 合格：非模板；有至少1个具体动作；有明确时间（符合上文定义）；并写明预期推进方向/结果。
   - 优秀（仅常规推进场景）：在合格基础上**同时**满足下列 a–d（缺一不可）；任一条仅“勉强沾边”则最高判合格：
     a. 至少2个明确可执行动作（可区分、可执行，体现可操作性）；
     b. 至少2个可定位的时间点（明确到天，可为两个截止时间，或「动作A在T1、动作B在T2」）；
     c. 至少1个**具体且可验证的目标结果**（完成后可明确判断是否达成），例如：确认范围/方案、完成演示、获取客户反馈、推动立项、签署合同等。注意：“沟通”“讨论”“对齐”“同步”等活动性描述不计为目标结果（它们是动作而非成果）；
     d. 计划整体体现**前瞻性和主动性**（如动作之间有逻辑递进关系、有明确的推进节奏，能体现对客户需求的理解）。

【收敛规则】
- 证据锚定：每个判断都必须能在原文找到直接证据（可逐字引用），不能脑补或推断。模板提示中的示例词不算证据。
- 冲突裁决：仅适用于「优秀」与「合格」之间边界不清时，**统一判「合格」**（评优秀宁缺毋滥）。不适用于「合格」与「不合格」：凡缺明确时间、缺具体动作或缺推进结果等硬条件，必须判「不合格」，禁止为了“折中”抬成合格。
- 编号/要点列表若承载真实计划，不判模板。
- “暂无/待定”或 TBD 单独出现视为占位符不合格；若在完整句中明确因商机关闭而无后续，可判合格。
- 严格从严：优秀的 a–c 条件刚好达到下限（如恰好2个动作、恰好2个时间点）且计划覆盖范围有限时，优先判“合格”而非“优秀”。优秀应当明显超过下限或具有显著的推进力度。
- 合格与优秀：目标仅为“沟通”“讨论”“同步”等活动性描述而无可验证结果时，不得评优秀。

【输出格式（仅 JSON）】
{{
  "next_steps_quality_zh": {{
    "level": "不合格|合格|优秀",
    "reason": "不超过50字，使用中文表述关键问题或亮点（若正文为英文，用中文概括对英文内容的评判）",
    "reason_en": "Concise English (one or two sentences), same judgment as level/reason; normal assessment wording"
  }}
}}

要求：
- 仅输出 JSON，不要任何前后缀、解释文字或 markdown。
- reason_en 为地道英文，与 level、reason 一致；禁止仅写「与中文一致」类占位。
- level、reason、reason_en 必须一致：若理由写明缺少时间安排/无明确时间等，等级必须为「不合格」；若目标仅为过程性确认，等级不得为「优秀」。
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
            response_format={"type": "json_object"},
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
            response_format={"type": "json_object"},
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