import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from sqlalchemy import text, Table, MetaData
from app.platforms.feishu.client import feishu_client
from app.platforms.lark.client import lark_client
from app.platforms.utils.url_parser import parse_bitable_url, resolve_bitable_app_token, get_platform_from_url
from app.platforms.constants import PLATFORM_FEISHU, PLATFORM_LARK
from sqlmodel import Session
from app.core.db import engine
from app.core.config import settings
from app.celery import app
from sqlalchemy.dialects.mysql import insert as mysql_insert

logger = logging.getLogger(__name__)

CRM_TABLE = 'crm_sales_visit_records'

# 配置区
FEISHU_URL = getattr(settings, 'FEISHU_BTABLE_URL', None)
url_type, url_token, table_id, view_id = parse_bitable_url(FEISHU_URL) if FEISHU_URL else (None, None, None, None)

# 根据URL自动检测平台
def get_platform_and_config():
    """
    根据bitable URL获取平台和配置信息
    
    Returns:
        (platform, app_id, app_secret, client) 的元组
    """
    if not FEISHU_URL:
        logger.warning("FEISHU_BTABLE_URL 未配置")
        return None, None, None, None
    
    platform = get_platform_from_url(FEISHU_URL)
    
    if platform == PLATFORM_FEISHU:
        app_id = settings.FEISHU_APP_ID
        app_secret = settings.FEISHU_APP_SECRET
        client = feishu_client
        logger.info(f"检测到飞书平台: {FEISHU_URL}")
    elif platform == PLATFORM_LARK:
        app_id = settings.LARK_APP_ID
        app_secret = settings.LARK_APP_SECRET
        client = lark_client
        logger.info(f"检测到Lark平台: {FEISHU_URL}")
    else:
        logger.warning(f"无法识别平台，默认使用飞书: {FEISHU_URL}")
        platform = PLATFORM_FEISHU
        app_id = settings.FEISHU_APP_ID
        app_secret = settings.FEISHU_APP_SECRET
        client = feishu_client
    
    return platform, app_id, app_secret, client

# 获取平台配置
PLATFORM, APP_ID, APP_SECRET, CLIENT = get_platform_and_config()


# 字段映射关系（Feishu字段名 -> DB字段名）
FIELD_MAP = {
    '客户名称': 'account_name',
    '客户ID': 'account_id',
    '合作伙伴': 'partner_name',
    '合作伙伴ID': 'partner_id',
    '商机名称': 'opportunity_name',
    '商机ID': 'opportunity_id',
    '客户/线索来源': 'customer_lead_source',
    '拜访对象类别': 'visit_object_category',
    '客户职位': 'contact_position',
    '客户名字': 'contact_name',
    '是否首次拜访': 'is_first_visit',
    '是否call high': 'is_call_high',
    '记录人': 'recorder',
    '记录人ID': 'recorder_id',
    '协同参与人': 'collaborative_participants',
    '拜访及沟通日期': 'visit_communication_date',
    '拜访及沟通方式': 'visit_communication_method',
    '拜访地点': 'counterpart_location',
    '沟通时长': 'communication_duration',
    '是/否达成预期': 'expectation_achieved',
    '跟进记录': 'followup_record',
    '跟进记录-zh': 'followup_record_zh',
    'AI判断结论-跟进记录': 'followup_quality_level_zh',
    'AI判断说明-跟进记录': 'followup_quality_reason_zh',
    '跟进记录-en': 'followup_record_en',
    'AI判断结论-跟进记录-en': 'followup_quality_level_en',
    'AI判断说明-跟进记录-en': 'followup_quality_reason_en',
    '下一步': 'next_steps',
    '下一步-zh': 'next_steps_zh',
    'AI判断结论-下一步': 'next_steps_quality_level_zh',
    'AI判断说明-下一步': 'next_steps_quality_reason_zh',
    '下一步-en': 'next_steps_en',
    'AI判断结论-下一步-en': 'next_steps_quality_level_en',
    'AI判断说明-下一步-en': 'next_steps_quality_reason_en',
    '备注': 'remarks',
    '附件': 'attachment',
    '父记录': 'parent_record',
    '拜访类型': 'visit_type',
    '拜访链接': 'visit_url',
    '拜访主题': 'subject',
    '跟进内容': 'followup_content',
    '拜访开始时间': 'visit_start_time',
    '拜访结束时间': 'visit_end_time',
}

CRM_FIELDS = list(FIELD_MAP.values()) + ['last_modified_time', 'record_id']

# 拉取bitable全量记录，包含系统自动生成字段，比如last_modified_time，record_id
def fetch_bitable_records(token, app_token, table_id, view_id, start_time=None, end_time=None, platform=None):
    """
    拉取bitable记录
    
    Args:
        token: 访问令牌
        app_token: 应用令牌
        table_id: 表格ID
        view_id: 视图ID
        start_time: 开始时间
        end_time: 结束时间
        platform: 平台名称 (feishu/lark)
    """
    # 根据平台选择API端点
    if platform == PLATFORM_LARK:
        base_url = "https://open.larksuite.com/open-apis"
        platform_name = "Lark"
    else:
        base_url = "https://open.feishu.cn/open-apis"
        platform_name = "飞书"
    
    url = f"{base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
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
            logger.error(f"拉取{platform_name}多维表格拜访记录失败: {resp.text}")
            break
        resp.raise_for_status()
        data = resp.json()["data"]
        items = data.get("items", [])
        records.extend(items)
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    
    logger.info(f"从{platform_name}拉取了 {len(records)} 条记录")
    return records

# 解析Feishu字段为DB字段
def parse_field_value(val, field_name=None):
    # 记录人、附件、父记录等特殊处理
    if field_name == '记录人':
        # 只取第一个记录人的 name
        if isinstance(val, list) and val and isinstance(val[0], dict):
            name = val[0].get('name', '')
            return name if name else ''
        # 如果直接是字符串，直接返回
        if isinstance(val, str):
            return val
        return ''
    if field_name == '记录人ID':
        # 处理UUID字符串，移除连字符以适配char(32)字段
        if isinstance(val, str):
            return val.replace('-', '')[:32]  # 移除连字符并截取前32位
        return ''
    if field_name == '附件':
        # 只取所有附件的 name
        if isinstance(val, list):
            names = [v.get('name', '') for v in val if isinstance(v, dict)]
            return ','.join(names) if names else ''
        if isinstance(val, str):
            return val
        return ''
    if field_name == '父记录':
        # 只存 link_record_ids 的字符串
        if isinstance(val, dict):
            link_ids = val.get('link_record_ids')
            if isinstance(link_ids, list):
                return ','.join(str(i) for i in link_ids if i)
            return str(link_ids) if link_ids else ''
        return ''
    if field_name == '协同参与人':
        # 处理协同参与人字段，支持JSON数组和字符串格式
        if isinstance(val, list):
            # 如果是列表，转换为JSON字符串存储
            import json
            return json.dumps(val, ensure_ascii=False)
        elif isinstance(val, str):
            # 如果是字符串，直接返回（可能是旧格式或已经是JSON字符串）
            return val
        elif isinstance(val, dict):
            # 如果是字典，转换为JSON字符串
            import json
            return json.dumps(val, ensure_ascii=False)
        return str(val) if val else ''
    if field_name in ['拜访开始时间', '拜访结束时间']:
        # 处理时间字段 - 直接存储为字符串
        if isinstance(val, str):
            # 直接返回字符串，不进行时区转换
            return val
        elif hasattr(val, 'strftime'):  # 检查是否是datetime类型
            # 如果是datetime类型，格式化为字符串
            return val.strftime('%Y-%m-%d %H:%M:%S')
        return val
    if isinstance(val, list):
        # 处理富文本/人员/多选等
        if val and isinstance(val[0], dict) and 'text' in val[0]:
            return ','.join([v['text'] for v in val if 'text' in v])
        else:
            return ','.join([str(v) for v in val])
    if isinstance(val, int) and len(str(val)) == 13:
        # 13位时间戳转日期，强制用北京时间
        dt = datetime.fromtimestamp(val // 1000, ZoneInfo("Asia/Shanghai"))
        return dt.strftime('%Y-%m-%d')
    if isinstance(val, dict):
        # 兜底：dict 不能直接入库
        return str(val)
    return val

def get_local_max_mtime(session):
    sql = f"SELECT MAX(UNIX_TIMESTAMP(last_modified_time) * 1000) FROM {CRM_TABLE} WHERE record_id LIKE 'rec%'"
    result = session.execute(text(sql)).scalar()
    return int(result) if result is not None else 0


@app.task(bind=True, max_retries=3)
def sync_bitable_visit_records(self):
    """
    定时同步多维表格拜访记录到crm_sales_visit_records
    支持飞书和Lark平台
    """
    try:
        # 检查配置
        if not PLATFORM or not APP_ID or not APP_SECRET or not CLIENT:
            logger.error("平台配置不完整，无法同步")
            return
        
        logger.info(f"开始同步{PLATFORM}多维表格拜访记录")
        
        # 获取访问令牌
        token = CLIENT.get_tenant_access_token()
        logger.info(f"获取到{PLATFORM}访问令牌: {token[:20]}...")
        
        # 解析应用令牌
        app_token = resolve_bitable_app_token(url_token)
        logger.info(f"应用令牌: {app_token}")
        
        # 获取本地最大修改时间
        with Session(engine) as session:
            local_max_mtime = get_local_max_mtime(session)
        logger.info(f"本地最大last_modified_time: {local_max_mtime}")
        
        # 拉取数据（可全量或按区间）
        records = fetch_bitable_records(token, app_token, table_id, view_id, platform=PLATFORM)
        logger.info(f"本次全量拉取{len(records)}条记录")
        
        # 过滤增量记录
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
        logger.error(f"同步{PLATFORM}多维表格拜访记录失败: {e}")
        raise self.retry(exc=e, countdown=60)

# 插入或更新记录
def upsert_visit_records(records):
    batch_time = datetime.now()
    metadata = MetaData()
    crm_table = Table(CRM_TABLE, metadata, autoload_with=engine)
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
        val = fields.get(feishu_key, None)
        if val in ("", None):
            mapped[db_key] = None
        else:
            mapped[db_key] = parse_field_value(val, field_name=feishu_key)
    modified_time = item.get('last_modified_time')
    if modified_time:
        mapped['last_modified_time'] = datetime.fromtimestamp(modified_time // 1000)
    else:
        mapped['last_modified_time'] = batch_time or datetime.now()
    mapped['record_id'] = item.get('record_id')
    return mapped
