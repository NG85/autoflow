import logging
from datetime import datetime
from app.tasks.bitable_import import FIELD_MAP, upsert_visit_records
from app.utils.uuid6 import uuid6
from app.core.config import settings
import requests
from app.feishu.common_open import DEFAULT_INTERNAL_GROUP_CHATS, send_feishu_message, get_tenant_access_token
from app.feishu.push_review import DEFAULT_EXTERNAL_GROUP_CHATS, DEFAULT_EXTERNAL_SALES

logger = logging.getLogger(__name__)

def _generate_form_record_id(now):
    dt = now.strftime("%Y%m%d")
    rand = uuid6().hex[:8]
    return f"form_{dt}_{rand}"

# 保存表单拜访记录到 crm_intermediate_import_visit_records
def save_visit_record_to_crm_table(record_schema, followup_level=None, followup_reason=None, next_steps_level=None, next_steps_reason=None):
    now = datetime.now()
    # 英文转中文
    fields = record_schema.dict(exclude_none=True)
    # 增加评判结果
    if followup_level is not None:
        fields['followup_quality_level'] = followup_level
    if followup_reason is not None:
        fields['followup_quality_reason'] = followup_reason
    if next_steps_level is not None:
        fields['next_steps_quality_level'] = next_steps_level
    if next_steps_reason is not None:
        fields['next_steps_quality_reason'] = next_steps_reason
    feishu_fields = {
        feishu_key: fields[db_key]
        for feishu_key, db_key in FIELD_MAP.items()
        if db_key in fields and fields[db_key] not in ("", None)
    }
    item = {
        "fields": feishu_fields,
        "last_modified_time": int(now.timestamp() * 1000),
        "record_id": _generate_form_record_id(now),
    }
    upsert_visit_records([item])

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
    评判跟进记录质量，返回等级（不合格/合格/优秀）和评判依据。
    """
    def parse_llm_result(text):
        text = text.replace('\n', '').replace('\r', '').strip()
        if "等级：" in text and "依据：" in text:
            try:
                level = text.split("等级：")[1].split("；")[0].strip()
                reason = text.split("依据：")[1].strip()
                return level, reason
            except Exception:
                return "不合格", "未能识别内容，请完善后重试"
        return "不合格", "未能识别内容，请完善后重试"

    followup_prompt = """
请根据以下标准对内容进行评判：
- 不合格：缺失要素（见下方要素要求），需指出缺失项
- 合格：要素齐备，描述清楚，能让他人理解本次拜访的过程和客户反馈
- 优秀：在合格基础上，内容具体、详实，包括关键细节（如沟通过程、客户原话、具体建议或异议等），能体现销售专业性和客户洞察

【要素要求】
1. 具体事情描述（如：做了什么/沟通了哪些内容/过程细节。请避免仅写“已沟通”等简单词语，尽量具体描述过程）
2. 客户反馈（如：客户的观点、态度、建议、异议等。请尽量具体，避免“无”或“还行”等模糊表达）

填写建议：请详细描述沟通过程和客户反馈，避免过于简略或模糊。例如：“与张三就产品功能进行了深入交流，客户提出希望增加自动化报表功能，并表示对现有方案基本认可。”

【回复要求】只输出“等级：xxx；依据：xxx”，如“等级：不合格；依据：缺失客户反馈”，或“等级：优秀；依据：要素齐备且描述详细”。
内容如下：
{followup_record}
"""
    followup_result = call_ark_llm(followup_prompt.format(followup_record=followup_record))
    return parse_llm_result(followup_result)


def check_next_steps_quality(next_steps):
    """
    评判下一步计划质量，返回等级（不合格/合格/优秀）和评判依据。
    """
    def parse_llm_result(text):
        text = text.replace('\n', '').replace('\r', '').strip()
        if "等级：" in text and "依据：" in text:
            try:
                level = text.split("等级：")[1].split("；")[0].strip()
                reason = text.split("依据：")[1].strip()
                return level, reason
            except Exception:
                return "不合格", "未能识别内容，请完善后重试"
        return "不合格", "未能识别内容，请完善后重试"

    next_steps_prompt = """
请根据以下标准对内容进行评判：
- 不合格：缺失要素（见下方要素要求），需指出缺失项
- 合格：三要素齐备，计划明确，时间节点具体，目标清晰
- 优秀：在合格基础上，计划具有可操作性和前瞻性，时间节点明确到天，目标具体且可衡量，能体现主动性和对客户需求的理解

【要素要求】
1. 代办事项说明（如：要做什么。请避免“后续跟进”等模糊表述，尽量具体）
2. 时间节点（如：预计完成时间、具体日期、星期、相对时间表达，如“下周三”、“明天”、“本周五”等。请尽量具体）
3. 达到目标（如：完成后希望达成的效果，例如完成产品演示、获取客户反馈、推进合同签署等）

填写建议：请明确写出下一步要做的事情、具体的时间节点和希望达成的目标，避免“待定”“后续跟进”等模糊表述。例如：“下周三前完成产品演示，争取客户确认技术方案。”

【回复要求】只输出“等级：xxx；依据：xxx”，如“等级：不合格；依据：缺失时间节点”，或“等级：优秀；依据：要素齐备且描述详细”。
内容如下：
{next_steps}
"""
    next_steps_result = call_ark_llm(next_steps_prompt.format(next_steps=next_steps))
    return parse_llm_result(next_steps_result)


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
    # 其他字段
    for k, v in sales_visit_record.items():
        if v is None:
            sales_visit_record[k] = "--"
    return sales_visit_record


def push_visit_record_feishu_message(external, sales_visit_record, receive_id=None, receive_id_type="chat_id"):
    sales_visit_record = fill_sales_visit_record_fields(sales_visit_record)
    if not external:
        logger.info(f"push visit record feishu message to internal group")
        receive_id = DEFAULT_INTERNAL_GROUP_CHATS[0]["chat_id"]
    elif sales_visit_record.get("recorder") in [user.get("name") for user in DEFAULT_EXTERNAL_SALES]:
        logger.info(f"push visit record feishu message to hanqiwei's external group")
        receive_id = DEFAULT_EXTERNAL_GROUP_CHATS[0]["chat_id"]
    else:
        logger.info(f"skip push visit record feishu message for {sales_visit_record.get('recorder')}")
        return
    template_id = "AAqII006k7Mt7" # 拜访记录卡片模板ID
    template_vars = {
        "visit_date": sales_visit_record["visit_communication_date"],
        "sales_visit_records": [sales_visit_record]
    }
    
    token = get_tenant_access_token(external=external)
    card_content = {
        "type": "template",
        "data": {
            "template_id": template_id,
            "template_variable": template_vars
        }
    }
    send_feishu_message(receive_id, token, card_content, receive_id_type=receive_id_type, msg_type="interactive")