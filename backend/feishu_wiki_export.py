from datetime import datetime
import os
import re
import requests
import csv
import argparse
import json
from collections import deque
import pandas as pd

# 配置区
# test
# APP_ID = os.getenv('FEISHU_APP_ID', 'cli_a7483c457f39100e')
# APP_SECRET = os.getenv('FEISHU_APP_SECRET', 'iGoBstZ0mSXakL6eJO3b1etKWuAytKI6')
# EXPORT_DIR = '/Users/gaona/data/feishu_wiki_export'

# # pingcap prod
# APP_ID = os.getenv('FEISHU_APP_ID', 'cli_a74bce3ec73d901c')
# APP_SECRET = os.getenv('FEISHU_APP_SECRET', '1xC7zUP6PQpUoOMJte8tddgPm5zaqfoW')
# EXPORT_DIR = f'/shared/data/feishu_wiki_export'

# 鞍羽
APP_ID = os.getenv('FEISHU_APP_ID', 'cli_a8ec9a7bddf81013')
APP_SECRET = os.getenv('FEISHU_APP_SECRET', 'giRHNJtHHwMKEe31MrRefMNj0zTR0mbt')
# EXPORT_DIR = f'/shared/data/feishu_wiki_export'
EXPORT_DIR = f'/Users/gaona/data/feishu_wiki_export'


# 工具函数

def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

# 1. 获取 tenant_access_token
def get_tenant_access_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    resp = requests.post(url, json={
        "app_id": app_id,
        "app_secret": app_secret
    })
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

# 2. 获取知识空间列表
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
        if resp.status_code != 200:
            print(f"获取知识空间列表失败 {resp.text}")
            return []
        resp.raise_for_status()
        data = resp.json()["data"]
        spaces.extend(data["items"])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return spaces

# 3. 获取知识空间节点（单层，支持分页，不递归）
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
    return nodes

# 4. 获取节点详细信息
def get_node_detail(token, node_token, obj_type=None):
    url = "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"token": node_token}
    if obj_type:
        params["obj_type"] = obj_type
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        print(f"访问节点详细信息失败 {node_token}")
        print(resp.json())
        return None
    resp.raise_for_status()
    return resp.json()["data"]

# 5. 获取文档内容（doc/docx）
def get_doc_content(token, doc_token):
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"访问文档失败 {doc_token}")
        print(resp.json())
        return None
    resp.raise_for_status()
    return resp.json()["data"]["content"]

# 5.1 获取电子表格内容（sheet）
def get_sheet_list(token, spreadsheet_token):
    url = f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"访问电子表格列表失败 {spreadsheet_token}")
        print(resp.json())
        return None
    resp.raise_for_status()
    return resp.json()["data"]["sheets"]

def get_single_sheet_content(token, spreadsheet_token, sheet_id):
    url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"访问电子表格内容失败 {spreadsheet_token}")
        print(resp.text)
        return None
    resp.raise_for_status()
    data = resp.json().get("data", {})
    # 兼容不同返回结构
    if "valueRange" in data and "values" in data["valueRange"]:
        return data["valueRange"]["values"]
    elif "values" in data:
        return data["values"]
    else:
        print("API返回结构异常:", data, resp.json())
        return None

# 5.2 获取多维表格内容（bitable）
def get_bitable_content(token, bitable_token):
    # 获取表格列表
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{bitable_token}/tables"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"访问多维表格失败 {bitable_token}")
        print(resp.json())
        return None
    resp.raise_for_status()
    tables = resp.json()["data"]["tables"]
    all_tables = []
    for table in tables:
        table_id = table["table_id"]
        table_name = table.get("name", table_id)
        # 获取字段
        field_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{bitable_token}/tables/{table_id}/fields"
        field_resp = requests.get(field_url, headers=headers)
        field_resp.raise_for_status()
        fields = field_resp.json()["data"]["fields"]
        field_names = [f["name"] for f in fields]
        # 获取记录
        records = []
        page_token = None
        while True:
            record_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{bitable_token}/tables/{table_id}/records"
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            record_resp = requests.get(record_url, headers=headers, params=params)
            record_resp.raise_for_status()
            data = record_resp.json()["data"]
            for rec in data["items"]:
                row = [rec["fields"].get(f, "") for f in field_names]
                records.append(row)
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        all_tables.append({"name": table_name, "fields": field_names, "records": records})
    return all_tables

# 6. 保存文本内容到本地
def save_text_to_file(content, path):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
        print(f"保存文本内容到文件: {path}")

# 6.1 保存csv内容
def save_csv_to_file(headers, rows, path):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', type=str, default=None, help='只导出大于此日期的节点，格式20240601或时间戳')
    parser.add_argument('--retry-failed', type=str, default=None, help='重试指定space_id的失败节点')
    args = parser.parse_args()
    since_ts = 0
    since_str = "00000000"
    if args.since:
        if len(args.since) == 8 and args.since.isdigit():
            since_ts = int(datetime.strptime(args.since, "%Y%m%d").timestamp())
            since_str = args.since
        elif args.since.isdigit():
            since_ts = int(args.since)
            since_str = datetime.fromtimestamp(since_ts).strftime("%Y%m%d")
    else:
        since_str = "00000000"
    return since_ts, since_str, args.retry_failed

def export_node(token, node, parent_path, exported_obj_tokens, link_visited=None, main_doc_title=None, since_ts=0, force_export=False):
    if link_visited is None:
        link_visited = set()
    # 1. 生成当前节点的本地路径
    title = safe_filename(node['title'])
    current_path = os.path.join(parent_path, title)
    obj_type = node['obj_type']
    obj_token = node['obj_token']
    if main_doc_title is None:
        main_doc_title = title
    # 判断是否新建/更新
    ts_fields = [node.get("obj_edit_time"), node.get("node_create_time"), node.get("obj_create_time")]
    ts_fields = [int(t) for t in ts_fields if t and str(t).isdigit()]
    latest_ts = max(ts_fields) if ts_fields else 0
    if not force_export and latest_ts <= since_ts:
        print(f"跳过未更新节点: {node['title']} ({obj_token})")
        return
    if obj_type in ['docx', 'sheet', 'bitable', 'slides']:
        if obj_token in exported_obj_tokens:
            print(f"跳过已导出: {title} ({obj_token}), {obj_type}")
            return
        exported_obj_tokens.add(obj_token)
        ensure_dir(parent_path)
        # 2. 保存内容
        if obj_type == 'docx':
            print(f"导出文档: {title} ({obj_token}), {obj_type}")
            md_content = get_doc_markdown(token, obj_token)
            if md_content:
                save_markdown_to_file(md_content, os.path.join(parent_path, f"{title}.md"))
            # 主文档内容保存在主目录下
            # if content:
            #     save_text_to_file(content, os.path.join(parent_path, f"{title}.txt"))
            # 递归处理文档块中的 link
            blocks = get_doc_blocks(token, obj_token)
            links = extract_links_from_blocks(blocks)
            if links:
                print(f"导出文档: {title} ({obj_token}), {obj_type} 包含 {len(links)} 个链接: {links}")
            # 引用内容统一保存在"主文档名/引用内容/"子目录下
            ref_dir = os.path.join(parent_path, title, '引用内容')
            for link in links:
                obj_type_hint = link["obj_type_hint"]
                linked_token = link["token"]
                linked_title = link["title"]
                url = link["url"]
                # 如果没有token，才用url解析
                if not linked_token and url:
                    doc_type2, linked_token = parse_feishu_url(url)
                    linked_title = None
                if not linked_token:
                    continue
                # 去重
                if (obj_type_hint, linked_token) in link_visited:
                    continue
                link_visited.add((obj_type_hint, linked_token))
                # 保存时优先用linked_title
                save_name = safe_filename(linked_title) if linked_title else linked_token
                # 递归处理
                if obj_type_hint == 16:
                    node_info = get_node_detail(token, linked_token)
                    if node_info is None:
                        continue
                    if "node" in node_info:
                        obj_type2 = node_info["node"]["obj_type"]
                        obj_token2 = node_info["node"]["obj_token"]
                        node_title = node_info["node"].get("title", obj_token2)
                    else:
                        obj_type2 = node_info.get("obj_type")
                        obj_token2 = node_info.get("obj_token")
                        node_title = node_info.get("title", obj_token2)
                    save_name2 = safe_filename(node_title)
                    if obj_type2 == "docx":
                        md_content2 = get_doc_markdown(token, obj_token2)
                        if md_content2:
                            save_markdown_to_file(md_content2, os.path.join(ref_dir, f"{save_name2}.md"))
                    elif obj_type2 == "sheet":
                        export_all_sheets(token, obj_token2, ref_dir, save_name2)
                    elif obj_type2 == "bitable":
                        tables = get_bitable_content(token, obj_token2)
                        if tables:
                            for table in tables:
                                table_title = safe_filename(table["name"])
                                file_path = os.path.join(ref_dir, f"{save_name2}_{table_title}.csv")
                                save_csv_to_file(table["fields"], table["records"], file_path)
                elif obj_type_hint == 1:
                    md_content2 = get_doc_markdown(token, linked_token)
                    if md_content2:
                        save_markdown_to_file(md_content2, os.path.join(ref_dir, f"{save_name}.md"))
                elif obj_type_hint == 3:
                    export_all_sheets(token, linked_token, ref_dir, save_name)
                elif obj_type_hint == 8:
                    tables = get_bitable_content(token, linked_token)
                    if tables:
                        for table in tables:
                            table_title = safe_filename(table["name"])
                            file_path = os.path.join(ref_dir, f"{save_name}_{table_title}.csv")
                            save_csv_to_file(table["fields"], table["records"], file_path)
                # 兼容未识别类型
                elif obj_type_hint is None:
                    doc_type2, _ = parse_feishu_url(url)
                    if doc_type2 == "docx":
                        md_content2 = get_doc_markdown(token, linked_token)
                        if md_content2:
                            save_markdown_to_file(md_content2, os.path.join(ref_dir, f"{save_name}.md"))
                    elif doc_type2 == "sheet":
                        export_all_sheets(token, linked_token, ref_dir, save_name)
                    elif doc_type2 == "bitable":
                        tables = get_bitable_content(token, linked_token)
                        if tables:
                            for table in tables:
                                table_title = safe_filename(table["name"])
                                file_path = os.path.join(ref_dir, f"{save_name}_{table_title}.csv")
                                save_csv_to_file(table["fields"], table["records"], file_path)
        elif obj_type == 'sheet':
            print(f"导出文档: {title} ({obj_token}), {obj_type}")
            export_all_sheets(token, obj_token, parent_path, title)
        elif obj_type == 'bitable':
            print(f"导出文档: {title} ({obj_token}), {obj_type}")
            tables = get_bitable_content(token, obj_token)
            if tables:
                for table in tables:
                    table_title = safe_filename(table['name'])
                    file_path = os.path.join(parent_path, f"{title}_{table_title}.csv")
                    save_csv_to_file(table['fields'], table['records'], file_path)
        elif obj_type == 'slides':
            print(f"暂不支持导出幻灯片: {title} ({obj_token}), {obj_type}")
            pass
    else:
        # 3. 目录节点，仅创建文件夹
        print(f"创建目录: {current_path}")
        ensure_dir(current_path)
    # 4. 递归处理子节点
    if node.get('has_child'):
        child_nodes = get_space_nodes(token, node['space_id'], node['node_token'])
        for child in child_nodes:
            export_node(token, child, current_path, exported_obj_tokens, link_visited, main_doc_title=main_doc_title, since_ts=since_ts, force_export=force_export)
            
# 新增：多sheet合并导出为xlsx
def export_spreadsheet_to_xlsx(token, spreadsheet_token, base_path, file_name_prefix=""):
    sheets = get_sheet_list(token, spreadsheet_token)
    if sheets is None:
        return
    file_path = os.path.join(base_path, f"{file_name_prefix or spreadsheet_token}.xlsx")
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        for sheet in sheets:
            sheet_id = sheet["sheet_id"]
            sheet_title = sheet.get("title", sheet_id)
            values = get_single_sheet_content(token, spreadsheet_token, sheet_id)
            if values:
                df = pd.DataFrame(values)
                # Excel sheet名最长31字符
                df.to_excel(writer, sheet_name=sheet_title[:31], index=False, header=False)
    print(f"已保存为多sheet Excel: {file_path}")

# 修改：原有export_all_sheets调用处，优先用xlsx导出
def export_all_sheets(token, spreadsheet_token, base_path, title_prefix=""):
    # 先合并导出为xlsx
    export_spreadsheet_to_xlsx(token, spreadsheet_token, base_path, title_prefix)
    # 如需保留每个sheet的csv，也可保留如下逻辑：
    sheets = get_sheet_list(token, spreadsheet_token)
    if sheets is None:
        return
    for sheet in sheets:
        sheet_id = sheet["sheet_id"]
        sheet_title = sheet.get("title", sheet_id)
        print(f"电子表格: sheet_id={sheet_id}, sheet_title={sheet_title}")
        values = get_single_sheet_content(token, spreadsheet_token, sheet_id)
        if values:
            headers = values[0] if len(values) > 0 else []
            rows = values[1:] if len(values) > 1 else []
            file_path = os.path.join(base_path, f"{title_prefix}_{safe_filename(sheet_title)}.csv")
            save_csv_to_file(headers, rows, file_path)

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

def fetch_linked_docs(token, url, visited=None, base_path=None):
    if visited is None:
        visited = set()
    doc_type, doc_token = parse_feishu_url(url)
    if not doc_token or (doc_type, doc_token) in visited:
        return
    visited.add((doc_type, doc_token))
    if doc_type == "docx":
        content = get_doc_content(token, doc_token)
        if base_path and content:
            save_text_to_file(content, os.path.join(base_path, f"{doc_token}.txt"))
        blocks = get_doc_blocks(token, doc_token)
        links = extract_links_from_blocks(blocks)
        for link in links:
            obj_type_hint = link["obj_type_hint"]
            linked_token = link["token"]
            linked_title = link["title"]
            url = link["url"]
            # 如果没有token，才用url解析
            if not linked_token and url:
                doc_type2, linked_token = parse_feishu_url(url)
                linked_title = None
            if not linked_token:
                continue
            # 去重
            if (obj_type_hint, linked_token) in visited:
                continue
            visited.add((obj_type_hint, linked_token))
            # 保存时优先用linked_title
            save_name = safe_filename(linked_title) if linked_title else linked_token
            # 递归处理
            if obj_type_hint == 16:
                node_info = get_node_detail(token, linked_token)
                if node_info is None:
                    continue
                if "node" in node_info:
                    obj_type2 = node_info["node"]["obj_type"]
                    obj_token2 = node_info["node"]["obj_token"]
                    node_title = node_info["node"].get("title", obj_token2)
                else:
                    obj_type2 = node_info.get("obj_type")
                    obj_token2 = node_info.get("obj_token")
                    node_title = node_info.get("title", obj_token2)
                save_name2 = safe_filename(node_title)
                if obj_type2 == "docx":
                    content2 = get_doc_content(token, obj_token2)
                    if content2:
                        save_text_to_file(content2, os.path.join(base_path, f"{save_name2}.txt"))
                elif obj_type2 == "sheet":
                    export_all_sheets(token, obj_token2, base_path, save_name2)
                elif obj_type2 == "bitable":
                    tables = get_bitable_content(token, obj_token2)
                    if tables:
                        for table in tables:
                            table_title = safe_filename(table["name"])
                            file_path = os.path.join(base_path, f"{save_name2}_{table_title}.csv")
                            save_csv_to_file(table["fields"], table["records"], file_path)
            elif obj_type_hint == 1:
                content2 = get_doc_content(token, linked_token)
                if content2:
                    save_text_to_file(content2, os.path.join(base_path, f"{save_name}.txt"))
            elif obj_type_hint == 3:
                export_all_sheets(token, linked_token, base_path, save_name)
            elif obj_type_hint == 8:
                tables = get_bitable_content(token, linked_token)
                if tables:
                    for table in tables:
                        table_title = safe_filename(table["name"])
                        file_path = os.path.join(base_path, f"{save_name}_{table_title}.csv")
                        save_csv_to_file(table["fields"], table["records"], file_path)
            # 兼容未识别类型
            elif obj_type_hint is None:
                # fallback到url解析
                doc_type2, _ = parse_feishu_url(url)
                if doc_type2 == "docx":
                    content2 = get_doc_content(token, linked_token)
                    if content2:
                        save_text_to_file(content2, os.path.join(base_path, f"{save_name}.txt"))
                elif doc_type2 == "sheet":
                    export_all_sheets(token, linked_token, base_path, save_name)
                elif doc_type2 == "bitable":
                    tables = get_bitable_content(token, linked_token)
                    if tables:
                        for table in tables:
                            table_title = safe_filename(table["name"])
                            file_path = os.path.join(base_path, f"{save_name}_{table_title}.csv")
                            save_csv_to_file(table["fields"], table["records"], file_path)
    elif doc_type == "sheet":
        # 电子表格内容获取
        sheets = get_sheet_list(token, doc_token)
        if base_path and sheets:
            for sheet in sheets:
                sheet_id = sheet["sheet_id"]
                sheet_title = safe_filename(sheet.get("title", sheet_id))
                values = get_single_sheet_content(token, doc_token, sheet_id)
                if values:
                    # values 是二维数组，通常第一行为表头
                    headers = values[0] if len(values) > 0 else []
                    rows = values[1:] if len(values) > 1 else []
                    file_path = os.path.join(base_path, f"{doc_token}_{sheet_title}.csv")
                    save_csv_to_file(headers, rows, file_path)
    elif doc_type == "bitable":
        tables = get_bitable_content(token, doc_token)
        if base_path and tables:
            for table in tables:
                table_title = safe_filename(table["name"])
                file_path = os.path.join(base_path, f"{doc_token}_{table_title}.csv")
                save_csv_to_file(table["fields"], table["records"], file_path)
    elif doc_type == "wiki_node":
        # 先获取节点信息，再递归
        node_info = get_node_detail(token, None, doc_token)
        obj_type = node_info["obj_type"]
        obj_token = node_info["obj_token"]
        # 递归抓取该节点实际内容
        if obj_type == "docx":
            fetch_linked_docs(token, f"https://xx.feishu.cn/docx/{obj_token}", visited, base_path)
        elif obj_type == "doc":
            fetch_linked_docs(token, f"https://xx.feishu.cn/docs/{obj_token}", visited, base_path)
        elif obj_type == "sheet":
            fetch_linked_docs(token, f"https://xx.feishu.cn/sheets/{obj_token}", visited, base_path)
        elif obj_type == "bitable":
            fetch_linked_docs(token, f"https://xx.feishu.cn/base/{obj_token}", visited, base_path)
        elif obj_type == "wiki_node":
            # 理论上不会出现，但可递归处理
            fetch_linked_docs(token, f"https://xx.feishu.cn/wiki/{obj_token}", visited, base_path)
    else:
        print(f"暂不支持的文档类型: {doc_type}, url: {url}")
        pass
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
        print(f"访问文档Markdown失败 {doc_token}")
        print(resp.text)
        return None
    resp.raise_for_status()
    return resp.json()["data"]["content"]

def save_markdown_to_file(content, path):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
        print(f"保存Markdown内容到文件: {path}")

# --- BFS遍历+断点续传 ---
def bfs_export_space_nodes(token, space_id, export_dir, since_ts=0):
    checkpoint_file = os.path.join(export_dir, f"checkpoint_{space_id}.json")
    failed_nodes = []
    failed_nodes_file = os.path.join(export_dir, f"failed_nodes_{space_id}.json")
    # 尝试恢复断点
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            checkpoint = json.load(f)
        # 恢复队列时，每个元素是 (node, parent_path)
        queue = deque([(node, parent_path) for node, parent_path in checkpoint["queue"]])
        exported_obj_tokens = set(checkpoint["exported_obj_tokens"])
        link_visited = set(tuple(x) for x in checkpoint.get("link_visited", []))
        print(f"恢复断点: {len(queue)}个待处理节点, {len(exported_obj_tokens)}个已导出对象")
    else:
        # 初始化队列，根节点入队，每个元素是 (node, parent_path)
        nodes = get_space_nodes(token, space_id)
        queue = deque([(node, export_dir) for node in nodes if not node.get('parent_node_token')])
        exported_obj_tokens = set()
        link_visited = set()
    while queue:
        node, parent_path = queue.popleft()
        try:
            export_node(token, node, parent_path, exported_obj_tokens, link_visited, since_ts=since_ts)
        except Exception as e:
            print(f"节点导出异常: {node.get('title')} {node.get('obj_token')}: {e}")
            failed_nodes.append({
                "title": node.get("title"),
                "obj_token": node.get("obj_token"),
                "obj_type": node.get("obj_type"),
                "parent_path": parent_path,
                "error": str(e)
            })
            # 立即写入，防止崩溃丢失
            with open(failed_nodes_file, "w", encoding="utf-8") as f:
                json.dump(failed_nodes, f, ensure_ascii=False, indent=2)
        # 子节点入队
        if node.get('has_child'):
            try:
                child_nodes = get_space_nodes(token, space_id, node['node_token'])
                # 子节点的parent_path为当前节点的本地路径
                current_path = os.path.join(parent_path, safe_filename(node['title']))
                for child in child_nodes:
                    queue.append((child, current_path))
            except Exception as e:
                print(f"子节点获取异常: {node.get('title')} {node.get('obj_token')}: {e}")
                failed_nodes.append({
                    "title": node.get("title"),
                    "obj_token": node.get("obj_token"),
                    "obj_type": node.get("obj_type"),
                    "parent_path": parent_path,
                    "error": f"子节点获取异常: {str(e)}"
                })
                with open(failed_nodes_file, "w", encoding="utf-8") as f:
                    json.dump(failed_nodes, f, ensure_ascii=False, indent=2)
        # 每处理完一个节点就保存断点
        # queue中每个元素是(node, parent_path)，node为dict，parent_path为str
        checkpoint = {
            "queue": [(n, p) for n, p in queue],
            "exported_obj_tokens": list(exported_obj_tokens),
            "link_visited": [list(x) for x in link_visited],
        }
        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    # 全部完成后删除断点文件
    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)
    # 结束后再写一次失败节点
    if failed_nodes:
        with open(failed_nodes_file, "w", encoding="utf-8") as f:
            json.dump(failed_nodes, f, ensure_ascii=False, indent=2)
        print(f"空间{space_id}导出完成，失败节点数: {len(failed_nodes)}，详见: {failed_nodes_file}")
    else:
        print(f"空间{space_id}导出完成，无失败节点")

def retry_failed_nodes(token, space_id, export_dir, since_ts=0):
    failed_nodes_file = os.path.join(export_dir, f"failed_nodes_{space_id}.json")
    if not os.path.exists(failed_nodes_file):
        print(f"未找到失败节点文件: {failed_nodes_file}")
        return
    with open(failed_nodes_file, "r", encoding="utf-8") as f:
        failed_nodes = json.load(f)
    if not failed_nodes:
        print("没有需要重试的失败节点。")
        return

    print(f"开始重试 {len(failed_nodes)} 个失败节点...")
    exported_obj_tokens = set()
    link_visited = set()
    still_failed = []
    for node_info in failed_nodes:
        try:
            node = {
                "title": node_info["title"],
                "obj_token": node_info["obj_token"],
                "obj_type": node_info["obj_type"],
                "space_id": space_id,
            }
            parent_path = node_info["parent_path"]
            export_node(token, node, parent_path, exported_obj_tokens, link_visited, since_ts=since_ts, force_export=True)
            print(f"重试成功: {node['title']} ({node['obj_token']})")
        except Exception as e:
            print(f"重试失败: {node_info['title']} ({node_info['obj_token']}): {e}")
            node_info["error"] = str(e)
            still_failed.append(node_info)
    with open(failed_nodes_file, "w", encoding="utf-8") as f:
        json.dump(still_failed, f, ensure_ascii=False, indent=2)
    print(f"重试完成，剩余失败节点数: {len(still_failed)}，详见: {failed_nodes_file}")

# 7. 主流程
def main():
    since_ts, since_str, retry_failed_space_id = parse_args()
    # end_str = datetime.now().strftime("%Y%m%d")
    end_str = "20250709"
    global EXPORT_DIR
    EXPORT_DIR = f'{EXPORT_DIR}_{since_str}_{end_str}'
    print(f"导出目录: {EXPORT_DIR}")
    print("获取tenant_access_token...")
    token = get_tenant_access_token(APP_ID, APP_SECRET)
    print(token)
    if retry_failed_space_id:
        ensure_dir(EXPORT_DIR)
        retry_failed_nodes(token, retry_failed_space_id, EXPORT_DIR, since_ts=since_ts)
        return
    # print("获取知识空间列表...")
    # spaces = get_space_list(token)
    # print(f"共{len(spaces)}个知识空间")
    # for space in spaces:
    #     space_id = space["space_id"]
    #     space_name = safe_filename(space.get("name", space_id))
    #     print(f"处理知识空间: {space_name} ({space_id})")
    #     ensure_dir(EXPORT_DIR)
    #     bfs_export_space_nodes(token, space_id, EXPORT_DIR, since_ts=since_ts)
    get_bitable_content(token, "LSQubTkQjajVNbscAlhcCUJjngb")

if __name__ == "__main__":
    main() 