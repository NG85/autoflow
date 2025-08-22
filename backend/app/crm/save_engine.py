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


def generate_bilingual_content(content: str, direction: str) -> str:
    """
    生成双语内容，支持中英文互译
    
    Args:
        content: 原始内容
        direction: 翻译方向
            - "to_zh": 转换为中文
            - "to_en": 转换为英文
        
    Returns:
        str: 翻译后的内容
    """
    if not content or content.strip() == "":
        return ""
    
    # 直接生成对应的翻译，不做语言检测
    if direction == "to_zh":
        prompt = f"""
请将以下内容翻译成中文，保持专业性和准确性：

{content}

请直接返回中文翻译结果，不要添加任何解释或说明。
"""
    elif direction == "to_en":
        prompt = f"""
请将以下内容翻译成英文，保持专业性和准确性：

{content}

请直接返回英文翻译结果，不要添加任何解释或说明。
"""
    else:
        return content
    
    try:
        result = call_ark_llm(prompt)
        return result.strip() if result else content
    except Exception as e:
        logger.warning(f"Failed to generate bilingual content: {e}")
        return content


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


def check_followup_quality(followup_record, language: str = "zh"):
    """
    评判跟进记录质量，返回等级（不合格/合格/优秀）和评判说明（问题点+建议）。
    
    Args:
        followup_record: 跟进记录内容
        language: 语言版本，"zh"为中文，"en"为英文
        
    Returns:
        tuple: (quality_level, quality_reason) 质量等级和评判说明
    """
    if language == "zh":
        prompt = f"""
你是一位销售管理专家，在销售团队管理和客户关系推进方面经验丰富，擅长评估销售人员记录的"跟进记录"质量。请对以下内容进行评判，并输出如下结构：
{{
  "level": "不合格/合格/优秀",
  "assessment": "根据不同等级填写：问题点+建议 / 优化建议 / 亮点评价）"
}}

【评判说明】
1. 拜访时间、对象、地点、参与人员等基础信息已通过表单其他字段填写，无需在"跟进记录"中重复描述，请专注于跟进内容本身的质量和推进效果。
2. 评判重点为沟通过程是否具体清晰、客户反馈是否明确具体、有无有效推进行为。
3. 不要求每次拜访都"达成共识"，但如有体现，可作为加分项。
4. 不应因未提及客户异议或反馈而直接判不合格，前提是过程描述具体且体现真实交流。
5. 若内容空泛但措辞华丽或无客户反馈，不得评为优秀。
6. 如有合理解释（如该项信息已由表单填写覆盖），请勿因缺失判不合格。

【评判要素】：
1. 内容完整性：
  - 过程描述：是否清楚说明了做了什么，沟通了哪些内容、有哪些过程细节？避免仅写"已沟通"等模糊词语。
  - 客户反馈：是否记录了客户的观点、态度、建议或异议？内容是否具体清晰，非敷衍。
2. 有效性：
  - 是否体现销售理解客户的需求及一定的推进动作？
3. 专业性/洞察力（加分项）：
  - 是否体现销售专业素养或客户洞察，如客户原话、内部动态、真实异议等。
4. 推进商机能力（加分项）：
  - 内容是否体现有效推进商机，如安排会议、推进试用、协调资源等。

【评判标准】
1. 不合格：缺失上述任一主要要素，或内容简略/模糊，无法体现有效沟通或客户反馈。
2. 合格：内容齐备，沟通过程清晰，能体现客户反馈和一定的推进动作，文字具体明了。
3. 优秀：在合格基础上，内容具体详实，体现专业性、客户洞察和强推进意识；避免空泛华丽表述。

【输出要求】
1. 如不合格，指出所有主要问题点，并提出具体、可执行的改进建议；
2. 如合格，给出优化建议（即使达标也要指出可提升之处）；
3. 如优秀，指出1-2个亮点，说明其在具体性、洞察力或推进力方面的优势；
4. 严格根据原始文本进行评判，不要虚构内容；
5. 严格只输出 JSON 格式，不要添加任何多余说明。

待评判内容如下：
{followup_record}
"""
    else:  # English version
        prompt = f"""
You are a sales management expert with extensive experience in sales team management and customer relationship advancement, skilled at evaluating the quality of "follow-up records" recorded by sales personnel. Please assess the following content and output in the following structure:
{{
  "level": "unqualified/qualified/excellent",
  "assessment": "Fill in according to different levels: issues + suggestions / optimization suggestions / highlight evaluation"
}}

[Assessment Guidelines]
1. Basic information such as visit time, target, location, and participants has been filled in through other form fields, so there's no need to repeat descriptions in the "follow-up record". Please focus on the quality of the follow-up content itself and its advancement effectiveness.
2. The assessment focuses on whether the communication process is specific and clear, whether customer feedback is clear and specific, and whether there are effective advancement actions.
3. It's not required to "reach consensus" in every visit, but if it's reflected, it can be a bonus point.
4. Don't directly judge as unqualified for not mentioning customer objections or feedback, provided the process description is specific and reflects genuine communication.
5. If the content is vague but uses flowery language or lacks customer feedback, it cannot be rated as excellent.
6. If there's a reasonable explanation (such as this information being covered by form fields), don't judge as unqualified due to omissions.

[Assessment Elements]:
1. Content Completeness:
  - Process Description: Does it clearly explain what was done, what content was communicated, and what process details were involved? Avoid vague terms like "communicated".
  - Customer Feedback: Does it record the customer's views, attitudes, suggestions, or objections? Is the content specific and clear, not perfunctory?
2. Effectiveness:
  - Does it reflect the salesperson's understanding of customer needs and certain advancement actions?
3. Professionalism/Insight (Bonus Points):
  - Does it reflect sales professional competence or customer insight, such as customer quotes, internal dynamics, genuine objections, etc.?
4. Opportunity Advancement Capability (Bonus Points):
  - Does the content reflect effective opportunity advancement, such as arranging meetings, promoting trials, coordinating resources, etc.?

[Assessment Criteria]
1. Unqualified: Missing any of the above main elements, or content is brief/vague, unable to reflect effective communication or customer feedback.
2. Qualified: Content is complete, communication process is clear, can reflect customer feedback and certain advancement actions, text is specific and clear.
3. Excellent: On the basis of qualified, content is specific and detailed, reflects professionalism, customer insight, and strong advancement awareness; avoid vague and flowery expressions.

[Output Requirements]
1. If unqualified, point out all main issues and provide specific, actionable improvement suggestions;
2. If qualified, give optimization suggestions (even if standards are met, point out areas for improvement);
3. If excellent, point out 1-2 highlights, explaining their advantages in specificity, insight, or advancement capability;
4. Strictly assess based on the original text, don't fabricate content;
5. Strictly output only in JSON format, don't add any extra explanations.

Content to assess:
{followup_record}
"""
    
    result = call_ark_llm(prompt)
    try:
        data = json.loads(result)
        if language == "zh":
            return data.get("level", "不合格"), data.get("assessment", "AI输出格式异常，请完善内容后重试")
        else:
            return data.get("level", "unqualified"), data.get("assessment", "AI output format error, please improve content and retry")
    except Exception:
        if language == "zh":
            return "不合格", "AI输出格式异常，请完善内容后重试"
        else:
            return "unqualified", "AI output format error, please improve content and retry"


def check_next_steps_quality(next_steps, language: str = "zh"):
    """
    评判下一步计划质量，返回等级（不合格/合格/优秀）和评判说明（问题点+建议）。
    
    Args:
        next_steps: 下一步计划内容
        language: 语言版本，"zh"为中文，"en"为英文
        
    Returns:
        tuple: (quality_level, quality_reason) 质量等级和评判说明
    """
    if language == "zh":
        prompt = f"""
你是一位销售管理专家，在销售团队管理和客户关系推进方面经验丰富，擅长评估销售人员记录的"下一步计划"质量。请对以下内容进行评判，并输出如下结构：
{{
  "level": "不合格/合格/优秀",
  "assessment": "根据不同等级填写：问题点+建议 / 优化建议 / 亮点评价）"
}}

【评判说明】
1. 本次评判仅聚焦"下一步计划"字段。其他字段如客户、项目、联系人等信息已通过表单结构记录，无需在此重复。
2. 请重点判断该计划是否明确、具体、具备可执行性，并是否能有效推进客户关系或商机进展。

【评判要素】
1. 计划明确性：
  - 是否写清楚要做什么？避免"持续沟通""保持联系"等泛泛描述，是否具体说明了任务/动作？
2. 时间安排：
  - 是否写明预期完成时间？可为具体日期或相对时间（如"下周二""近期""本周内"）。
  - 如完全缺失时间安排，评为不合格。
3. 目标导向（加分项）：
  - 是否表达了希望达成的具体目标？如"推进评审、完成演示、达成意向"等，体现主动推进意识。
4. 推进商机能力（加分项）：
  - 整体计划是否有助于推进客户沟通、转化或成交？

【评判标准】
1. 不合格：
  - 缺失关键要素（如无具体计划或无时间安排），或内容过于模糊/抽象，难以执行；
  - 示例：仅写"等待客户反馈""保持沟通"而无具体动作或时间。
2. 合格：
  - 写明了要做的事和时间节点，内容清晰、可执行，体现出基本的客户推进意识；
  - 示例："本周三前发送产品资料，并邀请客户安排下次演示。"
3. 优秀：
  - 在合格基础上，内容具体、详实，具备清晰的目标导向和较强的推进意识；
  - 示例："下周二与客户完成方案讲解，获取初步反馈，争取推动其内部技术评审。"

【输出要求】
1. 如内容不合格，指出所有主要问题点，并提出具体、可执行的改进建议；
2. 如内容合格，给出优化建议（即使达标也要指出可提升之处)；
3. 如内容优秀，指出1-2个突出亮点，说明其在计划性、目标性或推进力方面的优势；
4. 请严格只根据原始文本进行评判，不要虚构内容；
5. 严格只输出 JSON 格式，不添加任何解释说明。

待评判内容如下：
{next_steps}
"""
    else:  # English version
        prompt = f"""
You are a sales management expert with extensive experience in sales team management and customer relationship advancement, skilled at evaluating the quality of "next steps" recorded by sales personnel. Please assess the following content and output in the following structure:
{{
  "level": "unqualified/qualified/excellent",
  "assessment": "Fill in according to different levels: issues + suggestions / optimization suggestions / highlight evaluation"
}}

[Assessment Guidelines]
1. This assessment focuses only on the "next steps" field. Other fields such as customer, project, contact person information have been recorded through the form structure and need not be repeated here.
2. Please focus on judging whether the plan is clear, specific, executable, and whether it can effectively advance customer relationships or opportunity progress.

[Assessment Elements]
1. Plan Clarity:
  - Is it clear what needs to be done? Avoid vague descriptions like "continue communication" or "maintain contact", does it specifically explain the tasks/actions?
2. Time Arrangement:
  - Is the expected completion time specified? Can be specific dates or relative time (such as "next Tuesday", "recently", "within this week").
  - If time arrangement is completely missing, rate as unqualified.
3. Goal Orientation (Bonus Points):
  - Does it express specific goals to be achieved? Such as "advance review, complete demonstration, reach intention", etc., reflecting proactive advancement awareness.
4. Opportunity Advancement Capability (Bonus Points):
  - Does the overall plan help advance customer communication, conversion, or deal closure?

[Assessment Criteria]
1. Unqualified:
  - Missing key elements (such as no specific plan or no time arrangement), or content is too vague/abstract, difficult to execute;
  - Example: Only writing "wait for customer feedback" or "maintain communication" without specific actions or time.
2. Qualified:
  - Clearly states what needs to be done and time nodes, content is clear and executable, reflecting basic customer advancement awareness;
  - Example: "Send product materials before Wednesday and invite customer to arrange next demonstration."
3. Excellent:
  - On the basis of qualified, content is specific and detailed, with clear goal orientation and strong advancement awareness;
  - Example: "Complete solution presentation with customer next Tuesday, obtain initial feedback, and strive to promote their internal technical review."

[Output Requirements]
1. If content is unqualified, point out all main issues and provide specific, actionable improvement suggestions;
2. If content is qualified, give optimization suggestions (even if standards are met, point out areas for improvement);
3. If content is excellent, point out 1-2 outstanding highlights, explaining their advantages in planning, goal orientation, or advancement capability;
4. Please strictly judge only based on the original text, don't fabricate content;
5. Strictly output only in JSON format, don't add any explanatory notes.

Content to assess:
{next_steps}
"""
    
    result = call_ark_llm(prompt)
    try:
        data = json.loads(result)
        if language == "zh":
            return data.get("level", "不合格"), data.get("assessment", "AI输出格式异常，请完善内容后重试")
        else:
            return data.get("level", "unqualified"), data.get("assessment", "AI output format error, please improve content and retry")
    except Exception:
        if language == "zh":
            return "不合格", "AI输出格式异常，请完善内容后重试"
        else:
            return "unqualified", "AI output format error, please improve content and retry"


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