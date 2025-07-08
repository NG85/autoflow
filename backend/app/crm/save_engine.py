from datetime import datetime
from app.tasks.bitable_import import FIELD_MAP, upsert_visit_records
from app.utils.uuid6 import uuid6
from app.core.config import settings
import requests

def _generate_form_record_id(now):
    dt = now.strftime("%Y%m%d")
    rand = uuid6().hex[:8]
    return f"form_{dt}_{rand}"

# 保存表单拜访记录到 crm_intermediate_import_visit_records
def save_visit_record_to_crm_table(record_schema):
    now = datetime.now()
    
    # 英文转中文
    fields = record_schema.dict(exclude_none=True)
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


def check_record_qualified(db_session, followup_record, next_steps=None):
    """
    同时检查跟进记录和下一步计划，将所有缺失要素一次性合并返回。
    返回：(bool, str)，bool为是否通过，str为所有缺失要素的合并提示。
    """
    def clean_llm_result(text):
        return text.replace('\n', '').replace('\r', '').strip()

    # 检查跟进记录
    followup_prompt = """
请判断下面的内容是否同时包含以下2个要素：
1. 具体事情描述（如：拜访了谁/做了什么/沟通了哪些内容）
2. 客户反馈（如：客户的观点、态度、建议、异议等）
如有'达成共识'或明确结论可补充说明，但不是必需项。
【回复要求】如缺失要素，请只用“缺失xxx”中文短语回复（如：缺失客户反馈），多个缺失项用顿号分隔，不要加任何前缀、不要换行、不要加多余内容，否则只回复“齐全”。
内容如下：
{followup_record}
"""
    raw_result = call_ark_llm(followup_prompt.format(followup_record=followup_record))
    followup_result = clean_llm_result(raw_result)
    followup_msg = "" if followup_result == "齐全" else f"跟进记录：{followup_result}"

    # 检查下一步计划（如有）
    next_steps_msg = ""
    if next_steps and next_steps.strip():
        next_steps_prompt = """
请判断以下下一步计划内容是否同时包含以下3个要素：
1. 代办事项说明（如：要做什么）
2. 时间节点（如：预计完成时间、具体日期、星期、相对时间表达，如“下周三”、“明天”、“本周五”等）
3. 达到目标（如：完成后希望达成的效果）
【回复要求】如缺失要素，请只用“缺失xxx”中文短语回复（如：缺失时间节点），多个缺失项用顿号分隔，不要加任何前缀、不要换行、不要加多余内容，否则只回复“齐全”。
内容如下：
{next_steps}
"""
        raw_result = call_ark_llm(next_steps_prompt.format(next_steps=next_steps))
        next_steps_result = clean_llm_result(raw_result)
        if next_steps_result != "齐全":
            next_steps_msg = f"下一步计划：{next_steps_result}"

    # 合并所有缺失要素
    all_msgs = [msg for msg in [followup_msg, next_steps_msg] if msg]
    if all_msgs:
        return False, "；".join(all_msgs)
    return True, ""