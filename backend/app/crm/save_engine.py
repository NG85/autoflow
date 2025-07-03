from datetime import datetime
from typing import Optional
from uuid import UUID
from app.tasks.bitable_import import FIELD_MAP, upsert_visit_records
from app.utils.uuid6 import uuid6

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
