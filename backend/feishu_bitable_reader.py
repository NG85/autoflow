import os
import requests
import json
import datetime
from urllib.parse import urlparse, parse_qs

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
FEISHU_URL = 'https://test-dbk58s1t6pur.feishu.cn/base/Hg7bb7FW4aLDkbsnoUic0ntUnZe?table=tbl6nDugEoAulPsu&view=vewuBKoUMz'
url_type, url_token, table_id, view_id = parse_feishu_url(FEISHU_URL)
APP_ID = 'cli_a808bc341680d00b'
APP_SECRET = '9oGQcBaHRCfOB2Vy2AwtyGQxZUpPzjaa'
BITABLE_TOKEN = url_token
BITABLE_TABLE_ID = table_id
BITABLE_VIEW_ID = view_id
MTIME_FILE = 'bitable_last_mtime.txt'
print(url_type)
print(url_token)
print(table_id)
print(view_id)

def get_tenant_access_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    resp = requests.post(url, json={
        "app_id": app_id,
        "app_secret": app_secret
    })
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

def get_bitable_revision(token, app_token):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return int(resp.json()["data"]["app"].get("revision", 0))

def get_local_max_mtime():
    if os.path.exists(MTIME_FILE):
        with open(MTIME_FILE, 'r') as f:
            return int(f.read().strip())
    return 0

def save_local_max_mtime(mtime):
    with open(MTIME_FILE, 'w') as f:
        f.write(str(mtime))

def fetch_bitable_records(token, app_token, table_id, view_id=None):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    records = []
    page_token = None
    while True:
        body = {"page_size": 100, "automatic_fields": True}
        if page_token:
            body["page_token"] = page_token
        if view_id:
            body["view_id"] = view_id
        resp = requests.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            print(resp.text)
            break
        resp.raise_for_status()
        data = resp.json()["data"]
        records.extend(data["items"])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return records

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
            print(resp.text)
            return None
        resp.raise_for_status()
        data = resp.json().get("data", {})
        obj_token = data.get("node", {}).get("obj_token")
        if not obj_token:
            raise ValueError(f"未能通过wiki node获取到obj_token: {resp.text}")
        return obj_token
    # base/直接用
    return url_token

def main():
    if not BITABLE_TOKEN or not BITABLE_TABLE_ID:
        print("请设置环境变量 FEISHU_BITABLE_TOKEN 和 FEISHU_BITABLE_TABLE_ID")
        return
    print("获取tenant_access_token...")
    token = get_tenant_access_token(APP_ID, APP_SECRET)
    app_token = resolve_bitable_app_token(token, url_type, BITABLE_TOKEN)
    print(f"最终用于API的app_token: {app_token}")
    print("全量拉取表格数据...")
    local_max_mtime = get_local_max_mtime()
    print(f"本地最大last_modified_time: {local_max_mtime}")
    records = fetch_bitable_records(token, app_token, BITABLE_TABLE_ID, BITABLE_VIEW_ID)
    print(f"本次全量拉取{len(records)}条记录")
    # 本地过滤增量
    filtered = []
    max_mtime = local_max_mtime
    for rec in records:
        mtime = rec.get("last_modified_time", 0)
        if mtime and mtime > local_max_mtime:
            filtered.append(rec)
            if mtime > max_mtime:
                max_mtime = mtime
    print(f"本次需处理增量记录: {len(filtered)}")
    for rec in filtered:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    save_local_max_mtime(max_mtime)

if __name__ == "__main__":
    main() 