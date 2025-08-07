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
    {
        "name": "韩启微0", #赵雷
        "email": "",
        # "open_id": "ou_8c2d79f2db258064d98061b22d4cf9db", # Sia
        "open_id": "ou_63f923976c979e12cdaa9f43ff7dc5d6", # Sia 销售助理
    },
    {
        "name": "韩启微1", #朱振博
        "email": "zhuzhenbo@hotmail.com",
        # "open_id": "ou_258c1cca41c019c2057e0af499143b5e", # Sia
        "open_id": "ou_c26952dca84b0a02036d3b6c299642e7", # Sia 销售助理
    },
]

DEFAULT_MERGED_INTERNAL_ADMINS = DEFAULT_INTERNAL_ADMINS + [
    {
        "name": "高娜",
        "email": "",
        # "open_id": "ou_113e2a9993f3fc64e3861087756ee279", # Sia
        "open_id": "ou_7c7c1676c8e3e08a31ebd18864d5334f", # Sia 销售助理
    },
]

DEFAULT_INTERNAL_SALES = [
    {
        "name": "韩启微", #朱振博
        "email": "zhuzhenbo@hotmail.com",
        # "open_id": "ou_258c1cca41c019c2057e0af499143b5e", # Sia
        "open_id": "ou_c26952dca84b0a02036d3b6c299642e7", # Sia 销售助理
        "accounts": []
    },
    {
        "name": "金豫玮", #高娜
        "email": "",
        # "open_id": "ou_113e2a9993f3fc64e3861087756ee279", # Sia
        "open_id": "ou_7c7c1676c8e3e08a31ebd18864d5334f", # Sia 销售助理
        "accounts": []
    },
    {
        "name": "肖章博", #任小寅
        "email": "",
        # "open_id": "ou_43a1eaed720903a383fd9415759f895b", # Sia
        "open_id": "ou_d8febb66020e50b28e489ca47bd65b0e", # Sia 销售助理
    },
    {
        "name": "姚亮", #任小寅
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
    {
        "client_id": "cli_a735685d1c39100e",
        "name": "集结号",
        "chat_id": "oc_9b167146c8e0d78121898641fd91d61b"
    },
    # {
        # "client_id": "cli_a735685d1c39100e",
    #     "name": "Release",
    #     "chat_id": "oc_0b983a12f112ba3a8ae98cd3fd141d0e"
    # },
    {
        "client_id": "cli_a808bc341680d00b",
        "name": "拜访跟进",
        "chat_id": "oc_695eb4b33d0835d58181be4d47ab7494"
    },
]

# 获取tenant_access_token
def get_tenant_access_token(app_id: Optional[str] = None, app_secret: Optional[str] = None, external=False):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    resp = requests.post(url, json={
        "app_id": app_id or (settings.FEISHU_APP_ID if external else INTERNAL_APP_ID),
        "app_secret": app_secret or (settings.FEISHU_APP_SECRET if external else INTERNAL_APP_SECRET)
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
    # 飞书妙记
    m = re.match(r"https://[^/]+/minutes/([a-zA-Z0-9]+)", url)
    if m:
        return "minutes", m.group(1)
    return None, None

# 解析飞书多维表格/知识空间URL，返回(url_type, token, table_id, view_id)
def parse_feishu_bitable_url(url):
    """
    解析飞书多维表格/知识空间URL，返回(url_type, token, table_id, view_id)
    url_type: 'base' or 'wiki'
    """
    if not url:
        return None, None, None, None
    external = not url.startswith('https://mi5p6bgsnf8.feishu.cn/')
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
    return external, url_type, token, table_id, view_id

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
            logger.error(f"通过wiki node获取obj_token失败: {resp.text}")
            return None
        resp.raise_for_status()
        data = resp.json().get("data", {})
        obj_token = data.get("node", {}).get("obj_token")
        if not obj_token:
            raise ValueError(f"未能通过wiki node获取到obj_token: {resp.text}")
        logger.info(f"通过wiki node获取到obj_token: {obj_token}")
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

# 获取飞书妙记内容（minute）
def get_minutes_content(token, minute_token):
    try:
        # 使用转录接口获取妙记内容
        # 参考: https://open.feishu.cn/open-apis/minutes/v1/minutes/{minute_token}/transcript
        
        # 构建请求参数
        params = {
            "minute_token": minute_token,
            "need_speaker": "true",      # 包含发言人信息
            "need_timestamp": "true",    # 包含时间戳信息  
            "file_format": "txt"         # 文件格式，可选：txt, srt
        }
        
        url = f"https://open.feishu.cn/open-apis/minutes/v1/minutes/{minute_token}/transcript"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        resp = requests.get(url, headers=headers, params=params)
        
        if resp.status_code != 200:
            logger.error(f"访问妙记转录内容失败 {minute_token}, status: {resp.status_code}")
            logger.error(f"Response: {resp.text}")
            return None
        
        # 处理二进制流响应
        # 获取二进制内容并解码为UTF-8字符串
        content = resp.content.decode('utf-8')
        
        if content:
            logger.info(f"获取妙记内容成功, 内容长度: {len(content)}")
            return content
        else:
            logger.warning("获取妙记内容为空")
            return None
        
    except Exception as e:
        logger.error(f"获取妙记内容失败: {e}")
        return None


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
    if resp.status_code != 200:
        logger.error(f"发送飞书消息失败 {resp.text}, content: {content}")
    # resp.raise_for_status()
    return resp.json()

# 自定义异常类
class FeishuAuthError(Exception):
    """飞书授权异常"""
    pass

class UnsupportedDocumentTypeError(Exception):
    """不支持的文档类型异常"""
    pass

# 支持的文档类型
SUPPORTED_DOCUMENT_TYPES = {
    'doc', 'docx',      # 飞书文档
    'minutes',           # 飞书妙记
    'wiki_node',        # 知识库节点
}

# 暂不支持但可识别的类型
UNSUPPORTED_DOCUMENT_TYPES = {
    'sheet',            # 电子表格
    'bitable',          # 多维表格
}

def check_document_type_support(url_type: str, url: str) -> None:
    """
    检查文档类型是否支持
    
    Args:
        url_type: 解析出的文档类型
        url: 原始URL
        
    Raises:
        UnsupportedDocumentTypeError: 当文档类型不支持时抛出
    """
    if url_type in UNSUPPORTED_DOCUMENT_TYPES:
        type_names = {
            'sheet': '电子表格',
            'bitable': '多维表格'
        }
        type_name = type_names.get(url_type, url_type)
        raise UnsupportedDocumentTypeError(f"暂不支持{type_name}内容获取: {url}")
    
    if url_type not in SUPPORTED_DOCUMENT_TYPES:
        raise UnsupportedDocumentTypeError(f"不支持的飞书内容类型: {url_type}")

# 从非结构化内容中获取原文
def get_content_from_unstructured_source(access_token: str, url: str) -> tuple[Optional[str], Optional[str]]:
    """
    从非结构化内容中获取原文
    
    Args:
        access_token: 飞书访问令牌
        url: 内容链接（飞书文档、会议链接、文件URL等）
        
    Returns:
        tuple[content, document_type]: (原文内容, 文档类型)，如果获取失败则返回 (None, None)
    """
    try:
        # 判断URL类型
        if 'feishu.cn' in url or 'larksuite.com' in url:
            # 飞书相关链接
            return get_content_from_feishu_source(access_token, url)
        else:
            # TODO: 支持其他类型的链接（如文件URL）
            logger.warning(f"暂不支持的内容类型: {url}")
            return None, "file"
        
    except (FeishuAuthError, UnsupportedDocumentTypeError):
        # 重新抛出授权异常和不支持类型异常
        raise
    except Exception as e:
        logger.error(f"从非结构化内容获取失败: {e}")
        return None, None

# 从飞书相关链接中获取原文
def get_content_from_feishu_source(access_token: str, url: str) -> tuple[Optional[str], Optional[str]]:
    """
    从飞书相关链接中获取原文
    
    Args:
        access_token: 飞书访问令牌
        url: 飞书链接（文档、会议等）
        
    Returns:
        tuple[content, document_type]: (原文内容, 文档类型)，如果获取失败则返回 (None, None)
        
    Raises:
        FeishuAuthError: 当无法获取用户token时抛出
    """
    try:
        return get_content_from_feishu_source_with_token(url, access_token)
        
    except (FeishuAuthError, UnsupportedDocumentTypeError):
        # 重新抛出授权异常和不支持类型异常
        raise
    except Exception as e:
        logger.error(f"从飞书内容获取失败: {e}")
        return None, None


# 使用指定token从飞书相关链接中获取原文
def get_content_from_feishu_source_with_token(url: str, access_token: str) -> tuple[Optional[str], Optional[str]]:
    """
    使用指定的access token从飞书相关链接中获取原文
    
    Args:
        url: 飞书链接（文档、会议等）
        access_token: 飞书访问令牌
        
    Returns:
        tuple[content, document_type]: (原文内容, 文档类型)，如果获取失败则返回 (None, None)
        
    Raises:
        UnsupportedDocumentTypeError: 当文档类型不支持时抛出
    """
    try:
        # 解析URL类型
        url_type, doc_token = parse_feishu_url(url)
        if not url_type or not doc_token:
            logger.error(f"无法解析飞书URL: {url}")
            return None, None
        
        # 检查文档类型是否支持
        check_document_type_support(url_type, url)
        
        # 根据URL类型获取内容
        content = None
        if url_type in ['doc', 'docx']:
            # 文档类型
            content = get_doc_markdown(access_token, doc_token)
            if not content:
                content = get_doc_content(access_token, doc_token)
        elif url_type == 'minutes':
            # 飞书妙记类型
            content = get_minutes_content(access_token, doc_token)
        elif url_type == 'wiki_node':
            # 知识库节点类型
            node_info = get_node_detail(access_token, doc_token)
    
            if node_info is None:
                logger.error(f"无法获取知识库节点内容: {doc_token}")
                return None, url_type
            
            obj_type = None
            obj_token = None
            if "node" in node_info:
                obj_type = node_info["node"]["obj_type"]
                obj_token = node_info["node"]["obj_token"]
            else:
                obj_type = node_info.get("obj_type")
                obj_token = node_info.get("obj_token")
                
            if obj_type == "docx":
                content = get_doc_markdown(access_token, obj_token)
            else:
                logger.error(f"知识库节点类型不支持: {obj_type}")
                return None, url_type                        
                        
        if not content:
            logger.error(f"无法获取内容: {url}")
            return None, url_type
        logger.info(f"获取到内容: {content}")
        # 统一文档类型命名
        if url_type in ['doc', 'docx']:
            document_type = 'feishu_doc'
        elif url_type == 'minutes':
            document_type = 'feishu_minute'
        else:
            document_type = f'feishu_{url_type}'
        
        return content, document_type
        
    except UnsupportedDocumentTypeError:
        # 重新抛出不支持类型异常
        raise
    except Exception as e:
        logger.error(f"从飞书内容获取失败: {e}")
        return None, None
