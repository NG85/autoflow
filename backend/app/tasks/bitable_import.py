import logging
from datetime import datetime
import requests
from sqlalchemy import text, func, Table, MetaData
from sqlmodel import Session
from app.core.db import engine
from app.core.config import settings
from app.celery import app
from sqlalchemy.dialects.mysql import insert as mysql_insert
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

APP_ID = settings.FEISHU_APP_ID
APP_SECRET = settings.FEISHU_APP_SECRET

CRM_TABLE = 'crm_intermediate_import_visit_records'

# 在模块级别初始化Table对象
metadata = MetaData()
crm_table = Table(CRM_TABLE, metadata, autoload_with=engine)

def parse_feishu_url(url):
    """
    解析飞书多维表格/知识空间URL，返回(url_type, token, table_id, view_id)
    url_type: 'base' or 'wiki'
    """
    if not url:
        return None, None, None, None
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    url_type = None
    token = None
    for i, part in enumerate(path_parts):
        if part in ('base', 'wiki') and i + 1 < len(path_parts):
            url_type = part
            token = path_parts[i + 1]
            break
    qs = parse_qs(parsed.query)
    table_id = qs.get('table', [None])[0]
    view_id = qs.get('view', [None])[0]
    return url_type, token, table_id, view_id

# 配置区
FEISHU_URL = getattr(settings, 'FEISHU_BTABLE_URL', None)
url_type, url_token, table_id, view_id = parse_feishu_url(FEISHU_URL)


# 字段映射关系（Feishu字段名 -> DB字段名）
FIELD_MAP = {
    '拜访客户|伙伴名称': 'account_name',
    '客户CRM ID': 'account_id',
    '客户|线索来源': 'customer_lead_source',
    '拜访对象类别': 'visit_object_category',
    '客户职位': 'contact_position',
    '客户名字': 'contact_name',
    '记录人': 'recorder',
    '协同参与人': 'collaborative_participants',
    '拜访及沟通日期': 'visit_communication_date',
    '拜访或沟通地点': 'visit_communication_location',
    '对方所在地': 'counterpart_location',
    '拜访及沟通方式': 'visit_communication_method',
    '沟通时长': 'communication_duration',
    '是|否达成预期': 'expectation_achieved',
    '跟进记录': 'followup_record',
    '附件': 'attachment',
    '父记录': 'parent_record',
}

CRM_FIELDS = list(FIELD_MAP.values()) + ['last_modified_time', 'record_id']

# 获取tenant_access_token
def get_tenant_access_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    resp = requests.post(url, json={
        "app_id": app_id,
        "app_secret": app_secret
    })
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

# 拉取bitable全量记录，包含系统自动生成字段，比如last_modified_time，record_id
def fetch_bitable_records(token, app_token, table_id, view_id, start_time=None, end_time=None):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    records = []
    page_token = None
    filter_obj = None
    conditions = []
    if start_time:
        start_ts = str(int(start_time.timestamp()) * 1000)
        conditions.append({"field_name": "拜访及沟通日期", "operator": "isGreater", "value": [start_ts]})
    if end_time:
        end_ts = str(int(end_time.timestamp()) * 1000)
        conditions.append({"field_name": "拜访及沟通日期", "operator": "isLess", "value": [end_ts]})
    if conditions:
        filter_obj = {"conditions": conditions, "conjunction": "and"}
    while True:
        body = {"page_size": 100, "automatic_fields": True, "view_id": view_id}
        if filter_obj:
            body["filter"] = filter_obj
        if page_token:
            body["page_token"] = page_token
        resp = requests.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            logger.error(resp.text)
            break
        resp.raise_for_status()
        data = resp.json()["data"]
        items = data.get("items", [])
        records.extend(items)
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return records

# 解析Feishu字段为DB字段
def parse_field_value(val):
    if isinstance(val, list):
        # 处理富文本/人员/多选等
        if val and isinstance(val[0], dict) and 'text' in val[0]:
            return ','.join([v['text'] for v in val if 'text' in v])
        else:
            return ','.join([str(v) for v in val])
    if isinstance(val, int) and len(str(val)) == 13:
        # 13位时间戳转日期
        return datetime.fromtimestamp(val // 1000).strftime('%Y-%m-%d')
    return val

def get_local_max_mtime(session):
    sql = f"SELECT MAX(UNIX_TIMESTAMP(last_modified_time) * 1000) FROM {CRM_TABLE}"
    result = session.execute(text(sql)).scalar()
    return int(result) if result is not None else 0

def resolve_bitable_app_token(token, url_type, url_token):
    """
    根据url类型自动获取app_token：
    - base/xxx 直接用xxx
    - wiki/xxx 需GET https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?obj_type=wiki&token=xxx 拿obj_token
    """
    if not url_token:
        return None
    if url_type == 'wiki':
        node_token = url_token
        url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?obj_type=wiki&token={node_token}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error(resp.text)
            return None
        resp.raise_for_status()
        data = resp.json().get("data", {})
        obj_token = data.get("node", {}).get("obj_token")
        if not obj_token:
            raise ValueError(f"未能通过wiki node获取到obj_token: {resp.text}")
        return obj_token
    # base/直接用
    return url_token

@app.task(bind=True, max_retries=3)
def sync_bitable_visit_records(self):
    """
    定时同步飞书多维表格拜访记录到crm_intermediate_import_visit_records
    """
    try:
        logger.info("开始同步飞书多维表格拜访记录")
        token = get_tenant_access_token(APP_ID, APP_SECRET)
        app_token = resolve_bitable_app_token(token, url_type, url_token)
        with Session(engine) as session:
            local_max_mtime = get_local_max_mtime(session)
        logger.info(f"本地最大last_modified_time: {local_max_mtime}")
        # 拉取数据（可全量或按区间）
        records = fetch_bitable_records(token, app_token, table_id, view_id)
        logger.info(f"本次全量拉取{len(records)}条记录")
        filtered = []
        max_mtime = local_max_mtime
        for rec in records:
            mtime = rec.get("last_modified_time", 0)
            if mtime and mtime > local_max_mtime:
                filtered.append(rec)
                if mtime > max_mtime:
                    max_mtime = mtime
        logger.info(f"本次需同步增量记录: {len(filtered)}")
        if filtered:
            upsert_visit_records(filtered)
        else:
            logger.info("无需同步记录")
    except Exception as e:
        logger.error(f"同步飞书多维表格拜访记录失败: {e}")
        raise self.retry(exc=e, countdown=60)

# 插入或更新记录
def upsert_visit_records(records):
    batch_time = datetime.now()
    with Session(engine) as session:
        for rec in records:
            mapped = map_fields(rec, batch_time=batch_time)
            insert_stmt = mysql_insert(crm_table).values(**mapped)
            update_stmt = {k: mapped[k] for k in mapped if k != 'record_id'}
            if mapped.get('account_id') in (None, '', 'null'):
                update_stmt['account_id'] = text('account_id')
            ondup_stmt = insert_stmt.on_duplicate_key_update(**update_stmt)
            session.execute(ondup_stmt)
        session.commit()
    logger.info(f"已写入/更新{len(records)}条拜访记录到{CRM_TABLE}")

def map_fields(item, batch_time=None):
    fields = item.get('fields', {})
    mapped = {}
    for feishu_key, db_key in FIELD_MAP.items():
        mapped[db_key] = parse_field_value(fields.get(feishu_key, None))
    modified_time = item.get('last_modified_time')
    if modified_time:
        mapped['last_modified_time'] = datetime.fromtimestamp(modified_time // 1000)
    else:
        mapped['last_modified_time'] = batch_time or datetime.now()
    mapped['record_id'] = item.get('record_id')
    return mapped


 
