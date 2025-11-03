import logging
from datetime import datetime, timedelta, date
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
import pytz
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
DISPLAY_FIELD_MAP = {
    '唯一ID': 'record_id',
    '客户名称': 'account_name',
    '合作伙伴名称': 'partner_name',
    '商机名称': 'opportunity_name',
    '是否首次拜访': 'is_first_visit',
    '是否关键决策人拜访': 'is_call_high',
    '拜访及沟通日期': 'visit_communication_date',
    '负责销售': 'recorder',
    '联系人职位': 'contact_position',
    '联系人姓名': 'contact_name',
    '协同参与人（内部人员）': 'collaborative_participants',
    '拜访及沟通方式': 'visit_communication_method',
    '跟进记录': 'followup_record',
    'AI对跟进记录质量评估': 'followup_quality_level_zh',
    'AI对跟进记录质量评估详情': 'followup_quality_reason_zh',
    '下一步计划': 'next_steps',
    'AI对下一步计划质量评估': 'next_steps_quality_level_zh',
    'AI对下一步计划质量评估详情': 'next_steps_quality_reason_zh',
    '信息来源': 'visit_type',
    '备注': 'remarks',
    '创建时间': 'last_modified_time',
    '附件': 'attachment',
}
# 额外字段映射字典
EXTRA_FIELD_MAP = {
    '客户ID': 'account_id',
    '合作伙伴ID': 'partner_id',
    '商机ID': 'opportunity_id',
    '客户/线索来源': 'customer_lead_source',
    '拜访对象类别': 'visit_object_category',
    '负责销售ID': 'recorder_id',
    '拜访地点': 'counterpart_location',
    '沟通时长': 'communication_duration',
    '是/否达成预期': 'expectation_achieved',
    '跟进记录-zh': 'followup_record_zh',
    '跟进记录-en': 'followup_record_en',
    'AI对跟进记录质量评估-en': 'followup_quality_level_en',
    'AI对跟进记录质量评估详情-en': 'followup_quality_reason_en',
    '下一步计划-zh': 'next_steps_zh',
    '下一步计划-en': 'next_steps_en',
    'AI对下一步计划质量评估-en': 'next_steps_quality_level_en',
    'AI对下一步计划质量评估详情-en': 'next_steps_quality_reason_en',
    '父记录': 'parent_record',
    '拜访链接': 'visit_url',
    '拜访主题': 'subject',
    '跟进内容': 'followup_content',
    '拜访开始时间': 'visit_start_time',
    '拜访结束时间': 'visit_end_time',
}
# 拼接字段映射字典
FIELD_MAP = {**DISPLAY_FIELD_MAP, **EXTRA_FIELD_MAP}

CRM_FIELDS = list(FIELD_MAP.values())

# 拉取bitable全量记录
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
    # 负责销售、附件、父记录等特殊处理
    if field_name == '负责销售':
        # 只取第一个记录人的 name
        if isinstance(val, list) and val and isinstance(val[0], dict):
            name = val[0].get('name', '')
            return name if name else ''
        # 如果直接是字符串，直接返回
        if isinstance(val, str):
            return val
        return ''
    if field_name == '负责销售ID':
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
    if field_name == '协同参与人（内部人员）':
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


# @app.task(bind=True, max_retries=3)
def sync_from_bitable_visit_records(self):
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
        app_token = resolve_bitable_app_token(token, url_type, url_token)
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

# 反向字段映射（DB字段名 -> Feishu字段名），用于写入多维表格
DB_TO_FEISHU_FIELD_MAP = {v: k for k, v in FIELD_MAP.items()}

def build_bitable_fields_from_crm_row(crm_row: dict) -> dict:
    """
    将CRM表中的行数据（以DB字段命名）转换为Feishu/Lark多维表格可接受的fields结构。
    仅包含有值的字段。
    """
    feishu_fields = {}
    ts_fields = {"拜访及沟通日期", "创建时间"}
    bool_fields = {"是否首次拜访", "是否关键决策人拜访"}
    # 使用配置的时区
    writeback_tz = pytz.timezone(settings.CRM_WRITEBACK_TIMEZONE)
    def _to_millis(v):
        # 支持datetime、date、日期字符串、秒/毫秒时间戳
        if v is None or v == "":
            return None
        if isinstance(v, int):
            # 猜测位数：>= 10位。13位认为是毫秒，10位认为是秒
            s = len(str(abs(v)))
            if s >= 13:
                return int(v)
            if s >= 10:
                return int(v) * 1000
        if isinstance(v, float):
            # 作为秒
            return int(v * 1000)
        try:
            # date 对象处理（需要先转换为 datetime）
            if isinstance(v, date) and not isinstance(v, datetime):
                # 将 date 转换为 datetime，时间设为 00:00:00
                v = datetime.combine(v, datetime.min.time())
                v = writeback_tz.localize(v)
                return int(v.timestamp() * 1000)
            # datetime 对象处理
            if hasattr(v, 'timestamp'):
                # 如果是 naive datetime，添加时区信息
                if v.tzinfo is None:
                    v = writeback_tz.localize(v)
                return int(v.timestamp() * 1000)
            return v
        except Exception:
            return None
    for db_key, value in crm_row.items():
        # 单独处理recorder_department字段，映射到"部门"
        if db_key == 'recorder_department' and value not in (None, ""):
            feishu_fields["所在团队"] = value
            continue
        # 处理其他字段
        if db_key in DB_TO_FEISHU_FIELD_MAP and value not in (None, ""):
            feishu_key = DB_TO_FEISHU_FIELD_MAP[db_key]
            if feishu_key in ts_fields:
                ts = _to_millis(value)
                if ts is not None:
                    feishu_fields[feishu_key] = ts
            elif feishu_key in bool_fields:
                feishu_fields[feishu_key] = "是" if value else "否"
            elif feishu_key == "信息来源":
                feishu_fields[feishu_key] = "表单录入" if value == "form" else "会议录入"
            else:
                feishu_fields[feishu_key] = value
    return feishu_fields

def _bitable_base_url(platform: str | None) -> str:
    if platform == PLATFORM_LARK:
        return "https://open.larksuite.com/open-apis"
    return "https://open.feishu.cn/open-apis"

def batch_create_bitable_records(token: str, app_token: str, table_id: str, records_fields: list[dict], platform: str | None = None) -> list[str]:
    """
    批量在多维表格中创建记录，返回创建后的record_id列表。
    参考文档：批量新增多维表格记录（batch_create）。
    """
    if not records_fields:
        return []
    base_url = _bitable_base_url(platform)
    url = f"{base_url}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    created_ids: list[str] = []
    batch_size = 20
    for i in range(0, len(records_fields), batch_size):
        chunk = records_fields[i:i + batch_size]
        body = {"records": [{"fields": f} for f in chunk]}
        logger.info(f"批量创建{PLATFORM}多维表格拜访记录: {body}")
        resp = requests.post(url, headers=headers, json=body)
        logger.info(f"批量创建{PLATFORM}多维表格拜访记录响应: {resp.text}")
        resp.raise_for_status()
        resp_json = resp.json()
        if resp_json.get("code") != 0:
            logger.error(f"批量创建{PLATFORM}多维表格拜访记录失败: {resp_json.get('msg')}")
            return []
        data = resp_json.get("data", {})
        items = data.get("records") or []
        for item in items:
            rid = item.get("record_id") or item.get("id")
            if rid:
                created_ids.append(rid)
    return created_ids

@app.task(bind=True)
def sync_bitable_visit_records(self, start_date_str: str | None = None, end_date_str: str | None = None):
    """
    批量写入拜访记录到多维表格（飞书/Lark）。
    时间范围与CRM回写任务一致：
      - 当未传入起止日期时，按 settings.CRM_WRITEBACK_FREQUENCY 计算（DAILY: 昨天；WEEKLY: 上周日-本周六）。
      - 也可手动指定 start_date_str/end_date_str（YYYY-MM-DD）。
    返回创建后的 bitable record_id 列表。
    """
    try:
        if not PLATFORM or not APP_ID or not APP_SECRET or not CLIENT:
            logger.error("平台配置不完整，无法批量写入")
            return []

        # 计算日期范围
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                logger.info(f"Bitable写入任务，指定日期范围: {start_date} 到 {end_date}")
            except ValueError:
                logger.error(f"无效的日期格式: start_date={start_date_str}, end_date={end_date_str}")
                return []
        else:
            tz = pytz.timezone(settings.CRM_WRITEBACK_TIMEZONE)
            today = datetime.now(tz).date()
            frequency = settings.CRM_WRITEBACK_FREQUENCY
            from app.core.config import WritebackFrequency
            if frequency == WritebackFrequency.DAILY:
                start_date = today - timedelta(days=1)
                end_date = start_date
                logger.info(f"Bitable写入任务，按天模式，处理昨天: {start_date} (时区: {settings.CRM_WRITEBACK_TIMEZONE})")
            else:
                # WEEKLY: 上周日到本周六
                days_since_sunday = (today.weekday() + 1) % 7
                last_sunday = today - timedelta(days=days_since_sunday + 7)
                this_saturday = last_sunday + timedelta(days=6)
                start_date = last_sunday
                end_date = this_saturday
                logger.info(f"Bitable写入任务，按周模式，处理上周日到本周六: {start_date} 到 {end_date} (时区: {settings.CRM_WRITEBACK_TIMEZONE})")

        # 将本地时区的日期范围转换为UTC时间（数据库中last_modified_time是UTC时间）
        writeback_tz = pytz.timezone(settings.CRM_WRITEBACK_TIMEZONE)
        # 构建本地时区的开始和结束时间
        start_local = datetime.combine(start_date, datetime.min.time())
        end_local = datetime.combine(end_date, datetime.max.time())
        # 添加时区信息并转换为UTC
        start_local = writeback_tz.localize(start_local)
        end_local = writeback_tz.localize(end_local)
        start_dt_utc = start_local.astimezone(pytz.UTC)
        end_dt_utc = end_local.astimezone(pytz.UTC)
        # 移除时区信息（数据库中的datetime字段通常以naive UTC存储）
        start_dt = start_dt_utc.replace(tzinfo=None)
        end_dt = end_dt_utc.replace(tzinfo=None)

        # 查询指定时间范围内的CRM拜访记录
        with Session(engine) as session:
            # 构建字段列表（需要通过JOIN获取）
            cols = ", ".join([f"{CRM_TABLE}.{col}" for col in DISPLAY_FIELD_MAP.values()])
            # 添加部门字段（通过JOIN获取）
            sql = text(f"""
                SELECT {cols}, up.department AS recorder_department
                FROM {CRM_TABLE}
                LEFT JOIN user_profiles up ON up.user_id = {CRM_TABLE}.recorder_id
                WHERE {CRM_TABLE}.last_modified_time BETWEEN :start AND :end
            """)
            logger.info(f"查询指定时间范围内的CRM拜访记录: {sql}")
            rows = session.exec(sql, params={"start": start_dt, "end": end_dt}).fetchall()

        if not rows:
            logger.info("指定时间范围内没有需要写入的CRM拜访记录")
            return []

        # 构造写入字段
        crm_rows: list[dict] = []
        for r in rows:
            row_dict = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
            crm_rows.append(row_dict)

        token = CLIENT.get_tenant_access_token()
        app_token = resolve_bitable_app_token(token, url_type, url_token)

        records_fields = []
        for row in crm_rows:
            fields = build_bitable_fields_from_crm_row(row)
            if fields:
                records_fields.append(fields)

        if not records_fields:
            logger.warning("没有可写入的字段，跳过批量创建bitable记录")
            return []

        record_ids = batch_create_bitable_records(
            token=token,
            app_token=app_token,
            table_id=table_id,
            records_fields=records_fields,
            platform=PLATFORM,
        )
        logger.info(
            f"已在{PLATFORM}多维表格批量创建记录: {len(record_ids)} 条，范围 {start_date} 到 {end_date}"
        )
        return record_ids
    except Exception as e:
        logger.error(f"批量写入{PLATFORM}多维表格拜访记录失败: {e}")