import logging
import json
from typing import Optional
from datetime import datetime
from app.core.config import settings
from app.core.db import get_db_session
from app.api.routes.crm.models import VisitRecordCreate
from app.api.deps import CurrentUserDep, SessionDep
from app.tasks.bitable_import import FIELD_MAP, upsert_visit_records
from app.utils.uuid6 import uuid6
import requests
from app.repositories.document_content import DocumentContentRepo

logger = logging.getLogger(__name__)

def _generate_record_id(record_type, now):
    dt = now.strftime("%Y%m%d")
    rand = uuid6().hex[:8]
    return f"{record_type}_{dt}_{rand}"

# 保存表单拜访记录到 crm_sales_visit_records
def save_visit_record_to_crm_table(record_schema: VisitRecordCreate, db_session=None):
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
    
    # 如果提供了数据库会话，使用事务保存
    if db_session:
        # 直接使用传入的会话进行保存
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
    else:
        # 使用原有的方式保存
        upsert_visit_records([item])
    
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


def check_followup_quality(followup_record):
    """
    评判跟进记录质量，返回等级（不合格/合格/优秀）和评判说明（问题点+建议）。
    """
    prompt = f"""
你是一位销售管理专家，在销售团队管理和客户关系推进方面经验丰富，擅长评估销售人员记录的“跟进记录”质量。请对以下内容进行评判，并输出如下结构：
{{
  "等级": "不合格/合格/优秀",
  "评判说明": "根据不同等级填写：问题点+建议 / 优化建议 / 亮点评价）"
}}

【评判说明】
1. 拜访时间、对象、地点、参与人员等基础信息已通过表单其他字段填写，无需在“跟进记录”中重复描述，请专注于跟进内容本身的质量和推进效果。
2. 评判重点为沟通过程是否具体清晰、客户反馈是否明确具体、有无有效推进行为。
3. 不要求每次拜访都“达成共识”，但如有体现，可作为加分项。
4. 不应因未提及客户异议或反馈而直接判不合格，前提是过程描述具体且体现真实交流。
5. 若内容空泛但措辞华丽或无客户反馈，不得评为优秀。
6. 如有合理解释（如该项信息已由表单填写覆盖），请勿因缺失判不合格。

【评判要素】：
1. 内容完整性：
  - 过程描述：是否清楚说明了做了什么，沟通了哪些内容、有哪些过程细节？避免仅写“已沟通”等模糊词语。
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
    result = call_ark_llm(prompt)
    try:
        data = json.loads(result)
        return data.get("等级", "不合格"), data.get("评判说明", "AI输出格式异常，请完善内容后重试")
    except Exception:
        return "不合格", "AI输出格式异常，请完善内容后重试"


def check_next_steps_quality(next_steps):
    """
    评判下一步计划质量，返回等级（不合格/合格/优秀）和评判说明（问题点+建议）。
    """
    prompt = f"""
你是一位销售管理专家，在销售团队管理和客户关系推进方面经验丰富，擅长评估销售人员记录的“下一步计划”质量。请对以下内容进行评判，并输出如下结构：
{{
  "等级": "不合格/合格/优秀",
  "评判说明": "根据不同等级填写：问题点+建议 / 优化建议 / 亮点评价）"
}}

【评判说明】
1. 本次评判仅聚焦“下一步计划”字段。其他字段如客户、项目、联系人等信息已通过表单结构记录，无需在此重复。
2. 请重点判断该计划是否明确、具体、具备可执行性，并是否能有效推进客户关系或商机进展。

【评判要素】
1. 计划明确性：
  - 是否写清楚要做什么？避免“持续沟通”“保持联系”等泛泛描述，是否具体说明了任务/动作？
2. 时间安排：
  - 是否写明预期完成时间？可为具体日期或相对时间（如“下周二”“近期”“本周内”）。
  - 如完全缺失时间安排，评为不合格。
3. 目标导向（加分项）：
  - 是否表达了希望达成的具体目标？如“推进评审、完成演示、达成意向”等，体现主动推进意识。
4. 推进商机能力（加分项）：
  - 整体计划是否有助于推进客户沟通、转化或成交？

【评判标准】
1. 不合格：
  - 缺失关键要素（如无具体计划或无时间安排），或内容过于模糊/抽象，难以执行；
  - 示例：仅写“等待客户反馈”“保持沟通”而无具体动作或时间。
2. 合格：
  - 写明了要做的事和时间节点，内容清晰、可执行，体现出基本的客户推进意识；
  - 示例：“本周三前发送产品资料，并邀请客户安排下次演示。”
3. 优秀：
  - 在合格基础上，内容具体、详实，具备清晰的目标导向和较强的推进意识；
  - 示例：“下周二与客户完成方案讲解，获取初步反馈，争取推动其内部技术评审。”

【输出要求】
1. 如内容不合格，指出所有主要问题点，并提出具体、可执行的改进建议；
2. 如内容合格，给出优化建议（即使达标也要指出可提升之处)；
3. 如内容优秀，指出1-2个突出亮点，说明其在计划性、目标性或推进力方面的优势；
4. 请严格只根据原始文本进行评判，不要虚构内容；
5. 严格只输出 JSON 格式，不添加任何解释说明。

待评判内容如下：
{next_steps}
"""
    result = call_ark_llm(prompt)
    try:
        data = json.loads(result)
        return data.get("等级", "不合格"), data.get("评判说明", "AI输出格式异常，请完善内容后重试")
    except Exception:
        return "不合格", "AI输出格式异常，请完善内容后重试"


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
    
    # 处理是否call high字段
    is_call_high = sales_visit_record.get("is_call_high")
    if is_call_high is not None:
        sales_visit_record["is_call_high"] = "call high" if is_call_high else None
    # # AI评判不合格时，填充为"--"
    # if sales_visit_record.get("followup_quality_level") == '不合格':
    #     sales_visit_record["followup_quality_level"] = "--"
    # if sales_visit_record.get("next_steps_quality_level") == '不合格':
    #     sales_visit_record["next_steps_quality_level"] = "--"

    # 其他字段（排除特殊处理的字段）
    for k, v in sales_visit_record.items():
        if v is None and k not in ["is_first_visit", "is_call_high"]:
            sales_visit_record[k] = "--"
    return sales_visit_record


def push_visit_record_feishu_message(sales_visit_record, visit_type, db_session=None):
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
    
    # 使用飞书推送服务
    from app.services.feishu_notification_service import FeishuNotificationService
    
    notification_service = FeishuNotificationService()
    
    # 如果没有传入db_session，则创建一个新的
    should_close_session = False
    if db_session is None:
        db_session = get_db_session()
        should_close_session = True
    
    try:
        # 发送拜访记录通知
        result = notification_service.send_visit_record_notification(
            db_session=db_session,
            recorder_name=recorder_name,
            recorder_id=recorder_id,
            visit_record=sales_visit_record,
            visit_type=visit_type
        )
        
        if result["success"]:
            logger.info(f"Successfully pushed visit record notification: {result['message']}")
        else:
            logger.warning(f"Failed to push visit record notification: {result['message']}")
        
        return result["success"]
        
    except Exception as e:
        logger.error(f"Failed to push visit record feishu message: {e}")
        return False
    finally:
        # 只有当我们创建了session时才关闭它
        if should_close_session:
            db_session.close()


def save_visit_record_with_content(
    record: VisitRecordCreate,
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
    """
    try:
        # 先保存拜访记录以获取 record_id
        record_id = save_visit_record_to_crm_table(record, db_session)
        
        # 保存文档内容
        document_content_repo = DocumentContentRepo()
        document_content_repo.create_document_content(
            session=db_session,
            raw_content=content,
            document_type=document_type,
            source_url=record.visit_url,
            user_id=user.id,
            visit_record_id=record_id,
            title=title,
            auto_commit=False
        )
        
        # 提交事务
        db_session.commit()
        
        # 推送飞书消息
        record_data = record.model_dump()
        # 去掉attachment字段，避免传输过大的base64编码数据
        if "attachment" in record_data:
            logger.info("Removing attachment field from record data to avoid large base64 data transmission")
            del record_data["attachment"]
        
        push_visit_record_feishu_message(
            visit_type=record.visit_type,
            sales_visit_record=record_data,
            db_session=db_session
        )
        
        return {"code": 0, "message": "success", "data": {}}
        
    except Exception as e:
        # 如果保存失败，回滚事务
        db_session.rollback()
        logger.error(f"Failed to save visit record or document content: {e}")
        return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}