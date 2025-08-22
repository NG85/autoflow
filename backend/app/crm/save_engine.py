import logging
import json
import requests
from typing import Optional
from datetime import datetime
from app.core.config import settings
from app.core.db import get_db_session
from app.api.routes.crm.models import SimpleVisitRecordCreate, CompleteVisitRecordCreate
from app.api.deps import CurrentUserDep, SessionDep
from app.repositories.document_content import DocumentContentRepo
from app.services.platform_notification_service import platform_notification_service
from app.tasks.bitable_import import FIELD_MAP, upsert_visit_records
from app.utils.uuid6 import uuid6
logger = logging.getLogger(__name__)

def _generate_record_id(record_type, now):
    dt = now.strftime("%Y%m%d")
    rand = uuid6().hex[:8]
    return f"{record_type}_{dt}_{rand}"

# 保存表单拜访记录到 crm_sales_visit_records
def save_visit_record_to_crm_table(record_schema: SimpleVisitRecordCreate | CompleteVisitRecordCreate, db_session: SessionDep):
    now = datetime.now()
    # 英文转中文
    fields = record_schema.dict(exclude_none=True)
    feishu_fields = {
        feishu_key: fields[db_key]
        for feishu_key, db_key in FIELD_MAP.items()
        if db_key in fields and fields[db_key] not in ("", None)
    }
    record_id = _generate_record_id(record_schema.visit_type, now)
    item = {
        "fields": feishu_fields,
        "last_modified_time": int(now.timestamp() * 1000),
        "record_id": record_id,
    }
    
    # 使用事务保存
    batch_time = datetime.now()
    from app.tasks.bitable_import import map_fields, CRM_TABLE
    from sqlalchemy import MetaData, Table, text
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    
    metadata = MetaData()
    crm_table = Table(CRM_TABLE, metadata, autoload_with=db_session.bind)
    
    mapped = map_fields(item, batch_time=batch_time)
    insert_stmt = mysql_insert(crm_table).values(**mapped)
    update_stmt = {k: mapped[k] for k in mapped if k != 'record_id'}
    if mapped.get('account_id') in (None, '', 'null'):
        update_stmt['account_id'] = text('account_id')
    ondup_stmt = insert_stmt.on_duplicate_key_update(**update_stmt)
    db_session.execute(ondup_stmt)
    # 不在这里commit，由调用方控制事务
    
    return record_id

def call_ark_llm(prompt):
    api_key = settings.ARK_API_KEY
    model = settings.ARK_MODEL
    url = settings.ARK_API_URL
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    resp = requests.post(url, headers=headers, json=data, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    # 兼容不同返回结构
    return result["choices"][0]["message"]["content"] if "choices" in result else ""


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
        result = call_ark_llm(prompt)
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


def fill_sales_visit_record_fields(sales_visit_record):
    # 处理客户名称和合作伙伴
    account_name = sales_visit_record.get("account_name")
    partner_name = sales_visit_record.get("partner_name")
    if not account_name:
        if partner_name:
            sales_visit_record["account_name"] = partner_name
        else:
            sales_visit_record["account_name"] = "--"
    if not partner_name:
        sales_visit_record["partner_name"] = "--"
    # 协同参与人
    if sales_visit_record.get("collaborative_participants") is None:
        sales_visit_record["collaborative_participants"] = "--"
    
    # 处理是否首次拜访字段
    is_first_visit = sales_visit_record.get("is_first_visit")
    if is_first_visit is not None:
        sales_visit_record["is_first_visit"] = "首次拜访" if is_first_visit else None
        sales_visit_record["is_first_visit_en"] = "first visit" if is_first_visit else None
    
    # 处理是否call high字段
    is_call_high = sales_visit_record.get("is_call_high")
    if is_call_high is not None:
        sales_visit_record["is_call_high"] = "call high" if is_call_high else None
    
    # # AI评判不合格时，填充为"--"
    # if sales_visit_record.get("followup_quality_level") == '不合格':
    #     sales_visit_record["followup_quality_level"] = "--"
    # if sales_visit_record.get("next_steps_quality_level") == '不合格':
    #     sales_visit_record["next_steps_quality_level"] = "--"


    # 处理subject和subject_en字段 - 利用VisitSubject枚举的中英文支持
    from app.api.routes.crm.models import VisitSubject
    
    original_subject = sales_visit_record.get("subject")
    
    if original_subject is None or original_subject == "":
        sales_visit_record["subject"] = "--"
        sales_visit_record["subject_en"] = "--"
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
                sales_visit_record["subject_en"] = "--"

    # 其他字段（排除特殊处理的字段）
    for k, v in sales_visit_record.items():
        if v is None and k not in ["is_first_visit", "is_call_high"]:
            sales_visit_record[k] = "--"
    return sales_visit_record


def push_visit_record_message(sales_visit_record, visit_type, db_session=None, meeting_notes=None):
    sales_visit_record = fill_sales_visit_record_fields(sales_visit_record)
    
    # 获取记录人信息
    recorder_id = sales_visit_record.get("recorder_id")
    recorder_name = sales_visit_record.get("recorder")
    
    if not recorder_id and not recorder_name:
        logger.warning("No recorder_id or recorder_name found in sales visit record")
        return False
    
    # 去掉attachment字段，避免传输过大的base64编码数据
    if "attachment" in sales_visit_record:
        logger.info("Removing attachment field from visit record to avoid large base64 data transmission")
        del sales_visit_record["attachment"]
    
    # 确保会议纪要不为空
    if meeting_notes is None or meeting_notes == "":
        meeting_notes = "--"
    
    # 如果没有传入db_session，则创建一个新的
    should_close_session = False
    if db_session is None:
        db_session = get_db_session()
        should_close_session = True
    
    try:
        # 发送拜访记录通知
        result = platform_notification_service.send_visit_record_notification(
            db_session=db_session,
            recorder_name=recorder_name,
            recorder_id=recorder_id,
            visit_record=sales_visit_record,
            visit_type=visit_type,
            meeting_notes=meeting_notes
        )
        
        if result["success"]:
            logger.info(f"Successfully pushed visit record notification: {result['message']}")
        else:
            logger.warning(f"Failed to push visit record notification: {result['message']}")
        
        return result["success"]
        
    except Exception as e:
        logger.error(f"Failed to push visit record message: {e}")
        return False
    finally:
        # 只有当我们创建了session时才关闭它
        if should_close_session:
            db_session.close()


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
    record_id = save_visit_record_to_crm_table(record, db_session)
    
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
    
    # ========== 第二阶段：生成会议纪要总结（不影响主流程） ==========
    meeting_summary = None
    
    try:
        from app.services.meeting_summary_service import MeetingSummaryService
        meeting_summary_service = MeetingSummaryService()
        
        summary_result = meeting_summary_service.generate_meeting_summary(
            content=content,
            title=title,
            sales_name=record.recorder,
            account_name=record.account_name,
            contact_name=record.contact_name,
            contact_position=record.contact_position,
            visit_date=record.visit_communication_date,
            opportunity_name=record.opportunity_name,
            is_first_visit=record.is_first_visit,
            is_call_high=record.is_call_high
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
    
    # ========== 第三阶段：推送飞书消息（不影响事务） ==========
    try:
        record_data = record.model_dump()
        # 去掉attachment字段，避免传输过大的base64编码数据
        if "attachment" in record_data:
            logger.info("Removing attachment field from record data to avoid large base64 data transmission")
            del record_data["attachment"]
        
        # 推送飞书消息
        push_visit_record_message(
            visit_type=record.visit_type,
            sales_visit_record=record_data,
            db_session=db_session,
            meeting_notes=meeting_summary
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
        
        # 第二步：双语生成（批量处理）
        bilingual_result = generate_bilingual_content_batch(followup_record, next_steps)
        
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

请输出严格的JSON格式：
{{
  "followup_record_zh": "跟进记录的中文翻译",
  "followup_record_en": "Follow-up record English translation",
  "next_steps_zh": "下一步计划的中文翻译", 
  "next_steps_en": "Next steps English translation"
}}

重要提示：
- 如果原始内容为空，对应的翻译也为空字符串
- 优先使用目标语言的标点符号
- 专业术语、品牌名称、产品名称等可以保持原文
- **不要添加任何标签或前缀，只翻译内容本身**
- **英文翻译要求精炼：使用简洁句式，避免不必要的修饰词，保持信息完整性**
- 不要添加任何解释，只输出JSON
"""
    
    try:
        result = call_ark_llm(prompt)
        data = json.loads(result)
        
        logger.info(f"Bilingual content result: {data}")
        
        return {
            "followup_record_zh": data.get("followup_record_zh", ""),
            "followup_record_en": data.get("followup_record_en", ""),
            "next_steps_zh": data.get("next_steps_zh", ""),
            "next_steps_en": data.get("next_steps_en", "")
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


def assess_quality_batch(followup_record_zh: str, followup_record_en: str, next_steps_zh: str, next_steps_en: str) -> dict:
    """
    批量进行质量评估，按内容类型分组处理
    """
    result = {}
    
    # 第一步：评估跟进记录（中英双语）
    followup_result = assess_followup_quality_bilingual(followup_record_zh, followup_record_en)
    result.update(followup_result)
    
    # 第二步：评估下一步计划（中英双语）
    next_steps_result = assess_next_steps_quality_bilingual(next_steps_zh, next_steps_en)
    result.update(next_steps_result)
    
    return result


def assess_followup_quality_bilingual(followup_record_zh: str, followup_record_en: str) -> dict:
    """
    评估跟进记录质量（中英双语）
    """
    prompt = f"""
你是一位销售管理专家，在销售团队管理和客户关系推进方面经验丰富，擅长评估销售人员记录的"跟进记录"质量。请对以下内容进行评判，并输出如下结构：

**跟进记录（中文）**：
{followup_record_zh or ""}

**跟进记录（英文）**：
{followup_record_en or ""}

请输出严格的JSON格式：
{{
  "followup_quality_zh": {{
    "level": "合格/不合格/优秀",
    "reason": "质量评估原因"
  }},
  "followup_quality_en": {{
    "level": "qualified/unqualified/excellent",
    "reason": "Quality assessment reason"
  }}
}}

**评判说明**：
1. 拜访时间、对象、地点、参与人员等基础信息已通过表单其他字段填写，无需在"跟进记录"中重复描述，请专注于跟进内容本身的质量和推进效果。
2. 评判重点为沟通过程是否具体清晰、客户反馈是否明确具体、有无有效推进行为。
3. 不要求每次拜访都"达成共识"，但如有体现，可作为加分项。
4. 不应因未提及客户异议或反馈而直接判不合格，前提是过程描述具体且体现真实交流。
5. 若内容空泛但措辞华丽或无客户反馈，不得评为优秀。
6. 如有合理解释（如该项信息已由表单填写覆盖），请勿因缺失判不合格。

**评判要素**：
1. 内容完整性：
  - 过程描述：是否清楚说明了做了什么，沟通了哪些内容、有哪些过程细节？避免仅写"已沟通"等模糊词语。
  - 客户反馈：是否记录了客户的观点、态度、建议或异议？内容是否具体清晰，非敷衍。
2. 有效性：
  - 是否体现销售理解客户的需求及一定的推进动作？
3. 专业性/洞察力（加分项）：
  - 是否体现销售专业素养或客户洞察，如客户原话、内部动态、真实异议等。
4. 推进商机能力（加分项）：
  - 内容是否体现有效推进商机，如安排会议、推进试用、协调资源等。

**评判标准**：
1. 不合格：缺失上述任一主要要素，或内容简略/模糊，无法体现有效沟通或客户反馈。
2. 合格：内容齐备，沟通过程清晰，能体现客户反馈和一定的推进动作，文字具体明了。
3. 优秀：在合格基础上，内容具体详实，体现专业性、客户洞察和强推进意识；避免空泛华丽表述。

**输出要求**：
1. 如不合格，指出所有主要问题点，并提出具体、可执行的改进建议；
2. 如合格，给出优化建议（即使达标也要指出可提升之处）；
3. 如优秀，指出1-2个亮点，说明其在具体性、洞察力或推进力方面的优势；
4. 严格根据原始文本进行评判，不要虚构内容；
5. **严格只输出 JSON 格式，不要添加任何多余说明、解释或建议；**
6. **所有评估内容都必须包含在JSON结构内，不要在JSON外部添加任何文字；**
7. 如果内容为空，评估为"不合格"。
8. **重要：输出必须是完整的JSON格式，以{{开始，以}}结束，中间不能有任何其他文字。**
"""
    
    try:
        result = call_ark_llm(prompt)
        logger.info(f"Followup quality result: {result}")
        data = json.loads(result)
        return {
            "followup_quality_level_zh": data.get("followup_quality_zh", {}).get("level", "不合格"),
            "followup_quality_reason_zh": data.get("followup_quality_zh", {}).get("reason", "AI输出格式异常"),
            "followup_quality_level_en": data.get("followup_quality_en", {}).get("level", "unqualified"),
            "followup_quality_reason_en": data.get("followup_quality_en", {}).get("reason", "AI output format error")
        }
    except Exception as e:
        logger.warning(f"Failed to assess followup quality bilingual: {e}")
        return {
            "followup_quality_level_zh": "不合格",
            "followup_quality_reason_zh": "AI评估失败，请重试",
            "followup_quality_level_en": "unqualified",
            "followup_quality_reason_en": "AI assessment failed, please retry"
        }


def assess_next_steps_quality_bilingual(next_steps_zh: str, next_steps_en: str) -> dict:
    """
    评估下一步计划质量（中英双语）
    """
    prompt = f"""
你是一位销售管理专家，在销售团队管理和客户关系推进方面经验丰富，擅长评估销售人员记录的"下一步计划"质量。请对以下内容进行评判，并输出如下结构：

**下一步计划（中文）**：
{next_steps_zh or ""}

**下一步计划（英文）**：
{next_steps_en or ""}

请输出严格的JSON格式：
{{
  "next_steps_quality_zh": {{
    "level": "合格/不合格/优秀",
    "reason": "质量评估原因"
  }},
  "next_steps_quality_en": {{
    "level": "qualified/unqualified/excellent",
    "reason": "Quality assessment reason"
  }}
}}

**评判说明**：
1. 本次评判仅聚焦"下一步计划"字段。其他字段如客户、项目、联系人等信息已通过表单结构记录，无需在此重复。
2. 请重点判断该计划是否明确、具体、具备可执行性，并是否能有效推进客户关系或商机进展。

**评判要素**：
1. 计划明确性：
  - 是否写清楚要做什么？避免"持续沟通""保持联系"等泛泛描述，是否具体说明了任务/动作？
2. 时间安排：
  - 是否写明预期完成时间？可为具体日期或相对时间（如"下周二""近期""本周内"）。
  - 如完全缺失时间安排，评为不合格。
3. 目标导向（加分项）：
  - 是否表达了希望达成的具体目标？如"推进评审、完成演示、达成意向"等，体现主动推进意识。
4. 推进商机能力（加分项）：
  - 整体计划是否有助于推进客户沟通、转化或成交？

**评判标准**：
1. 不合格：
  - 缺失关键要素（如无具体计划或无时间安排），或内容过于模糊/抽象，难以执行；
  - 示例：仅写"等待客户反馈""保持沟通"而无具体动作或时间。
2. 合格：
  - 写明了要做的事和时间节点，内容清晰、可执行，体现出基本的客户推进意识；
  - 示例："本周三前发送产品资料，并邀请客户安排下次演示。"
3. 优秀：
  - 在合格基础上，内容具体、详实，具备清晰的目标导向和较强的推进意识；
  - 示例："下周二与客户完成方案讲解，获取初步反馈，争取推动其内部技术评审。"

**输出要求**：
1. 如内容不合格，指出所有主要问题点，并提出具体、可执行的改进建议；
2. 如内容合格，给出优化建议（即使达标也要指出可提升之处)；
3. 如内容优秀，指出1-2个突出亮点，说明其在计划性、目标性或推进力方面的优势；
4. 严格根据原始文本进行评判，不要虚构内容；
5. **严格只输出 JSON 格式，不要添加任何多余说明、解释或建议；**
6. **所有评估内容都必须包含在JSON结构内，不要在JSON外部添加任何文字；**
7. 如果内容为空，评估为"不合格"。
8. **重要：输出必须是完整的JSON格式，以{{开始，以}}结束，中间不能有任何其他文字。**
"""
    
    try:
        result = call_ark_llm(prompt)
        logger.info(f"Next steps quality result: {result}")
        data = json.loads(result)
        return {
            "next_steps_quality_level_zh": data.get("next_steps_quality_zh", {}).get("level", "不合格"),
            "next_steps_quality_reason_zh": data.get("next_steps_quality_zh", {}).get("reason", "AI输出格式异常"),
            "next_steps_quality_level_en": data.get("next_steps_quality_en", {}).get("level", "unqualified"),
            "next_steps_quality_reason_en": data.get("next_steps_quality_en", {}).get("reason", "AI output format error")
        }
    except Exception as e:
        logger.warning(f"Failed to assess next steps quality bilingual: {e}")
        return {
            "next_steps_quality_level_zh": "不合格",
            "next_steps_quality_reason_zh": "AI评估失败，请重试",
            "next_steps_quality_level_en": "unqualified",
            "next_steps_quality_reason_en": "AI assessment failed, please retry"
        }