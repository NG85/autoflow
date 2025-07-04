from typing import Optional
from urllib.parse import parse_qs, urlparse
import logging
import requests
import json
import re
from app.core.config import settings

logger = logging.getLogger(__name__)

HOST = settings.REVIEW_REPORT_HOST
# Sia 销售助理
# INTERNAL_APP_ID = 'cli_a735685d1c39100e'
# INTERNAL_APP_SECRET = 'EUIECISu75Ctk30VW3aFlqJGUInBQGzb'

# Sia
INTERNAL_APP_ID = 'cli_a74a312d91b9d00d'
INTERNAL_APP_SECRET = 'kkiilUjGS79NPL1vVUZywc7cKojThgjE'


DEFAULT_INTERNAL_ADMINS = [
    # {
    #     "name": "赵雷",
    #     "email": "",
    #     # "open_id": "ou_8c2d79f2db258064d98061b22d4cf9db", # Sia
    #     "open_id": "ou_63f923976c979e12cdaa9f43ff7dc5d6", # Sia 销售助理
    # },
    {
        "name": "朱振博",
        "email": "zhuzhenbo@hotmail.com",
        # "open_id": "ou_258c1cca41c019c2057e0af499143b5e", # Sia
        "open_id": "ou_c26952dca84b0a02036d3b6c299642e7", # Sia 销售助理
    },
    {
        "name": "高娜",
        "email": "",
        # "open_id": "ou_113e2a9993f3fc64e3861087756ee279", # Sia
        "open_id": "ou_7c7c1676c8e3e08a31ebd18864d5334f", # Sia 销售助理
        "accounts": []
    },
]

DEFAULT_INTERNAL_SALES = [
    {
        "name": "高娜",
        "email": "",
        # "open_id": "ou_113e2a9993f3fc64e3861087756ee279", # Sia
        "open_id": "ou_7c7c1676c8e3e08a31ebd18864d5334f", # Sia 销售助理
        "accounts": []
    },
    {
        "name": "任小寅",
        "email": "",
        # "open_id": "ou_43a1eaed720903a383fd9415759f895b", # Sia
        "open_id": "ou_d8febb66020e50b28e489ca47bd65b0e", # Sia 销售助理
        "accounts": []
    },
]

DEFAULT_INTERNAL_USERS = [
    {
        "name": "朱振博",
        "email": "zhuzhenbo@hotmail.com",
        # "open_id": "ou_258c1cca41c019c2057e0af499143b5e", # Sia
        "open_id": "ou_c26952dca84b0a02036d3b6c299642e7", # Sia 销售助理
    },
    {
        "name": "高娜",
        "email": "",
        # "open_id": "ou_113e2a9993f3fc64e3861087756ee279", # Sia
        "open_id": "ou_7c7c1676c8e3e08a31ebd18864d5334f", # Sia 销售助理
    },
    {
        "name": "任小寅",
        "email": "tigeren@live.com",
        # "open_id": "ou_43a1eaed720903a383fd9415759f895b", # Sia
        "open_id": "ou_d8febb66020e50b28e489ca47bd65b0e", # Sia 销售助理
    },
]

DEFAULT_INTERNAL_GROUP_CHATS = [
    # {
    #     "name": "产品需求开发讨论群",
    #     "chat_id": "oc_b6cf36c6c6dad851e53a01b5fa3554be"
    # },
    {
        "name": "Release",
        "chat_id": "oc_0b983a12f112ba3a8ae98cd3fd141d0e"
    }
]

# 获取tenant_access_token
def get_tenant_access_token(app_id: Optional[str] = None, app_secret: Optional[str] = None, external=False):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    resp = requests.post(url, json={
        "app_id": app_id or settings.FEISHU_APP_ID if external else INTERNAL_APP_ID,
        "app_secret": app_secret or settings.FEISHU_APP_SECRET if external else INTERNAL_APP_SECRET
    })
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

# 解析飞书URL，返回(url_type, token)
def parse_feishu_url(url):
    # 新版文档
    m = re.match(r"https://[^/]+/docx/([a-zA-Z0-9]+)", url)
    if m:
        return "docx", m.group(1)
    # 旧版文档
    m = re.match(r"https://[^/]+/docs/([a-zA-Z0-9]+)", url)
    if m:
        return "doc", m.group(1)
    # 电子表格
    m = re.match(r"https://[^/]+/sheets/([a-zA-Z0-9]+)", url)
    if m:
        return "sheet", m.group(1)
    # 多维表格
    m = re.match(r"https://[^/]+/base/([a-zA-Z0-9]+)", url)
    if m:
        return "bitable", m.group(1)
    # 知识库节点
    m = re.match(r"https://[^/]+/wiki/([a-zA-Z0-9]+)", url)
    if m:
        return "wiki_node", m.group(1)
    return None, None

# 解析飞书多维表格/知识空间URL，返回(url_type, token, table_id, view_id)
def parse_feishu_bitable_url(url):
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

# 根据多维表格的url类型（base/wiki）自动获取app_token
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

# 获取知识空间列表
def get_space_list(token):
    url = "https://open.feishu.cn/open-apis/wiki/v2/spaces"
    headers = {"Authorization": f"Bearer {token}"}
    spaces = []
    page_token = None
    while True:
        params = {"page_size": 20}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()["data"]
        spaces.extend(data["items"])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return spaces

# 获取知识空间节点（递归）
def get_space_nodes(token, space_id, parent_node_token=None):
    url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{space_id}/nodes"
    headers = {"Authorization": f"Bearer {token}"}
    nodes = []
    page_token = None
    while True:
        params = {"page_size": 50}
        if parent_node_token:
            params["parent_node_token"] = parent_node_token
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()["data"]
        nodes.extend(data["items"])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    # 递归获取子节点
    all_nodes = []
    for node in nodes:
        all_nodes.append(node)
        if node.get("has_child"):
            child_nodes = get_space_nodes(token, space_id, node["node_token"])
            all_nodes.extend(child_nodes)
    return all_nodes

# 获取空间节点详细信息
def get_node_detail(token, node_token, obj_type=None):
    url = "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"token": node_token}
    if obj_type:
        params["obj_type"] = obj_type
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        logger.error(f"访问节点详细信息失败 {node_token}")
        logger.error(resp.text)
        return None
    resp.raise_for_status()
    return resp.json()["data"]

# 获取文档纯文本内容（doc/docx）
def get_doc_content(token, doc_token):
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        logger.error(f"访问文档失败 {doc_token}")
        logger.error(resp.text)
        return None
    resp.raise_for_status()
    return resp.json()["data"]["content"]


# 获取文档所有块（doc/docx）
def get_doc_blocks(token, doc_token):
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/blocks"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"page_size": 100}
    blocks = []
    has_more = True
    page_token = None
    while has_more:
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()["data"]
        blocks.extend(data["items"])
        has_more = data.get("has_more", False)
        page_token = data.get("page_token")
    return blocks

# 从文档块中提取链接信息
def extract_links_from_blocks(blocks):
    links = []
    for block in blocks:
        if "text" in block and "elements" in block["text"]:
            for elem in block["text"]["elements"]:
                if "mention_doc" in elem:
                    md = elem["mention_doc"]
                    links.append({
                        "url": md.get("url"),
                        "obj_type_hint": md.get("obj_type"),
                        "token": md.get("token"),
                        "title": md.get("title")
                    })
                if "link" in elem and "url" in elem["link"]:
                    links.append({
                        "url": elem["link"]["url"],
                        "obj_type_hint": None,
                        "token": None,
                        "title": None
                    })
    return links

# 获取新版云文档内容（docx的markdown格式）
def get_doc_markdown(token, doc_token):
    url = f"https://open.feishu.cn/open-apis/docs/v1/content"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "doc_token": doc_token,
        "doc_type": "docx",
        "content_type": "markdown"
    }
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        logger.error(f"访问文档Markdown失败 {doc_token}")
        logger.error(resp.text)
        return None
    resp.raise_for_status()
    return resp.json()["data"]["content"]


# 获取电子表格内容（spreadsheet）
def get_sheet_list(token, spreadsheet_token):
    url = f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        logger.error(f"访问电子表格列表失败 {spreadsheet_token}")
        logger.error(resp.json())
        return None
    resp.raise_for_status()
    return resp.json()["data"]["sheets"]

# 获取电子表格内单个表单内容（sheet）
def get_single_sheet_content(token, spreadsheet_token, sheet_id):
    url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        logger.error(f"访问电子表格内容失败 {spreadsheet_token}")
        logger.error(resp.text)
        return None
    resp.raise_for_status()
    return resp.json()["data"]["valueRange"]["values"]


# 发送飞书消息
def send_feishu_message(receive_id, token, text, receive_id_type="open_id", msg_type="text"):
    api_url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    content = {"text": text} if msg_type == "text" else text
    payload = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False)
    }
    params = {"receive_id_type": receive_id_type}
    resp = requests.post(api_url, params=params, headers=headers, data=json.dumps(payload))
    resp.raise_for_status()
    return resp.json()
