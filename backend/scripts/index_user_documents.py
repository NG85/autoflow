import enum
import os
import json
import requests
from typing import List, Dict, Set
from itertools import islice
from dataclasses import dataclass
from datetime import datetime
import argparse
import time
import logging
import sys

# 配置日志
def setup_logging():
    """
    配置日志记录
    """
    # 创建日志目录
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # 创建日志记录器
    logger = logging.getLogger('upload_script')
    logger.setLevel(logging.INFO)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    # 创建文件处理器
    log_file = os.path.join(log_dir, f'upload_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 创建格式化器
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    
    # 添加处理器到记录器
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    # 记录日志文件路径
    logger.info(f"日志文件保存在: {log_file}")
    
    return logger

# 创建全局日志记录器
logger = setup_logging()

class MimeTypes(str, enum.Enum):
    PLAIN_TXT = "text/plain"
    MARKDOWN = "text/markdown"
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    CSV = "text/csv"
    

# 定义支持的文件扩展名
SUPPORTED_EXTENSIONS: Set[str] = {
    '.txt', '.md', '.markdown', '.pdf', '.docx', '.pptx', '.xlsx', '.csv'
}

# 文件大小限制（100MB）
MAX_FILE_SIZE = 100 * 1024 * 1024

@dataclass
class Upload:
    id: int
    name: str
    size: int
    path: str
    mime_type: str
    meta: Dict

def format_size(size: int) -> str:
    """
    格式化文件大小
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

def get_mime_type(file_path: str) -> str:
    """
    根据文件扩展名获取MIME类型
    """
    ext = os.path.splitext(file_path)[1].lower()
    mime_types = {
        '.pdf': MimeTypes.PDF,
        '.docx': MimeTypes.DOCX,
        '.xlsx': MimeTypes.XLSX,
        '.pptx': MimeTypes.PPTX,
        '.txt': MimeTypes.PLAIN_TXT,
        '.md': MimeTypes.MARKDOWN,
        '.markdown': MimeTypes.MARKDOWN,
        '.csv': MimeTypes.CSV,
    }
    return mime_types.get(ext, MimeTypes.PLAIN_TXT)

def is_supported_file(file_path: str) -> bool:
    """
    检查文件是否支持上传
    """
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_EXTENSIONS

def validate_file(file_path: str) -> bool:
    """
    验证文件是否满足上传要求
    """
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        return False
        
    if not is_supported_file(file_path):
        logger.error(f"不支持的文件类型: {file_path}")
        return False
        
    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_SIZE:
        logger.error(f"文件过大 ({format_size(file_size)}): {file_path}")
        return False
        
    return True

def get_all_files(directory: str) -> List[str]:
    """
    递归获取目录下的所有支持的文件
    """
    all_files = []
    skipped_files = []
    
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            if os.path.isfile(file_path):
                if validate_file(file_path):
                    all_files.append(file_path)
                else:
                    skipped_files.append(file_path)
    
    if skipped_files:
        logger.warning("以下文件将被跳过：")
        for file in skipped_files:
            logger.warning(f"- {file}")
        logger.warning(f"共跳过 {len(skipped_files)} 个文件")
    
    return all_files

def batch_files(file_paths: List[str], batch_size: int):
    """
    将文件路径列表分批
    """
    iterator = iter(file_paths)
    while batch := list(islice(iterator, batch_size)):
        yield batch

def upload_files(base_url: str, cookie: str = None, authorization: str = None, file_paths: List[str] = None, meta: str = None) -> List[Upload]:
    """
    上传一批文件
    """
    upload_url = f"{base_url}/admin/uploads"
    
    # 准备文件列表
    files = []
    for file_path in file_paths:
        if os.path.isfile(file_path):
            mime_type = get_mime_type(file_path)
            file_size = os.path.getsize(file_path)
            logger.info(f"准备上传: {file_path}")
            logger.debug(f"  - 类型: {mime_type}")
            logger.debug(f"  - 大小: {format_size(file_size)}")
            files.append(('files', (os.path.basename(file_path), open(file_path, 'rb'), mime_type)))
    
    # 准备请求头和数据
    headers = {}
    if cookie:
        headers['Cookie'] = cookie
    if authorization:
        headers['Authorization'] = f'Bearer {authorization}'
    
    # 使用传入的meta或默认值
    default_meta = {"category": "playbook"}
    meta_data = json.loads(meta) if meta else default_meta
    data = {
        'meta': json.dumps(meta_data)
    }
    
    try:
        # 发送请求
        response = requests.post(upload_url, headers=headers, data=data, files=files, timeout=300)  # 5分钟超时
        
        if response.status_code != 200:
            error_msg = f"上传失败: {response.text}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        # 将响应转换为Upload对象列表
        uploads = []
        for item in response.json():
            upload = Upload(
                id=item['id'],
                name=item['name'],
                size=item['size'],
                path=item['path'],
                mime_type=item['mime_type'],
                meta=item['meta']
            )
            uploads.append(upload)
        
        return uploads
    except requests.exceptions.Timeout:
        error_msg = "上传请求超时"
        logger.error(error_msg)
        raise Exception(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"上传请求异常: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)
    finally:
        # 确保关闭所有打开的文件
        for _, (_, file, _) in files:
            file.close()

def create_datasource(base_url: str, cookie: str = None, authorization: str = None, kb_id: int = None, name: str = None, batch_index: int = None, uploads: List[Upload] = None) -> Dict:
    """
    创建datasource
    """
    datasource_url = f"{base_url}/admin/knowledge_bases/{kb_id}/datasources"
    
    # 准备config
    config = []
    for upload in uploads:
        config.append({
            "file_name": upload.name,
            "file_id": upload.id,
            "meta": upload.meta
        })
    
    # 生成带时间戳的名称
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    datasource_name = f"{name}_{timestamp}_batch_{batch_index + 1}"
    
    # 准备请求数据
    data = {
        "name": datasource_name,
        "data_source_type": "file",
        "config": config
    }
    
    headers = {
        'Content-Type': 'application/json'
    }
    if cookie:
        headers['Cookie'] = cookie
    if authorization:
        headers['Authorization'] = f'Bearer {authorization}'
    
    try:
        # 发送请求
        response = requests.post(datasource_url, headers=headers, json=data, timeout=60)  # 1分钟超时
        
        if response.status_code != 200:
            error_msg = f"创建数据源失败: {response.text}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        return response.json()
    except requests.exceptions.Timeout:
        error_msg = "创建数据源请求超时"
        logger.error(error_msg)
        raise Exception(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"创建数据源请求异常: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)

def process_directory(base_url: str, cookie: str = None, authorization: str = None, directory: str = None, kb_id: int = None, base_name: str = None, batch_size: int = 5, meta: str = None):
    """
    处理目录下的所有文件，分批上传并创建数据源
    """
    # 获取所有文件路径（包括子目录）
    all_files = get_all_files(directory)
    total_files = len(all_files)
    total_batches = (total_files + batch_size - 1) // batch_size
    
    logger.info(f"找到 {total_files} 个文件，将分 {total_batches} 批处理")
    
    start_time = time.time()
    for batch_index, current_batch in enumerate(batch_files(all_files, batch_size)):
        batch_start_time = time.time()
        try:
            logger.info(f"处理第 {batch_index + 1}/{total_batches} 批")
            logger.info(f"上传 {len(current_batch)} 个文件...")
            
            # 上传文件
            uploads = upload_files(base_url, cookie, authorization, current_batch, meta)
            logger.info(f"成功上传 {len(uploads)} 个文件")
            
            # 创建数据源
            logger.info(f"创建第 {batch_index + 1} 批的数据源...")
            result = create_datasource(base_url, cookie, authorization, kb_id, base_name, batch_index, uploads)
            logger.info(f"成功创建数据源: {result['name']}")
            
            batch_time = time.time() - batch_start_time
            logger.info(f"本批处理完成，耗时: {batch_time:.2f}秒")
            
        except Exception as e:
            logger.error(f"处理第 {batch_index + 1} 批时出错: {str(e)}")
            continue
    
    total_time = time.time() - start_time
    logger.info(f"所有处理完成，总耗时: {total_time:.2f}秒")

def validate_directory(directory: str) -> str:
    """
    验证并规范化目录路径
    """
    # 将相对路径转换为绝对路径
    abs_path = os.path.abspath(directory)
    
    # 检查目录是否存在
    if not os.path.exists(abs_path):
        error_msg = f"目录不存在: {abs_path}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # 检查是否是目录
    if not os.path.isdir(abs_path):
        error_msg = f"路径不是目录: {abs_path}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    return abs_path

def main():
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='自动处理用户文档并构建索引')
    
    # 添加命令行参数
    parser.add_argument('--base-url', required=True, help='API基础URL')
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument('--cookie', help='认证Cookie')
    auth_group.add_argument('--authorization', help='Bearer Token认证')
    parser.add_argument('--directory', required=True, help='要处理的文档目录，支持相对路径（如 . 表示当前目录）')
    parser.add_argument('--kb-id', required=True, type=int, help='知识库ID')
    parser.add_argument('--name', help='数据源基础名称，默认使用目录名')
    parser.add_argument('--batch-size', type=int, default=5, help='每批处理的文件数量，默认为5')
    parser.add_argument('--meta', help='自定义meta信息，JSON格式字符串，例如：\'{"category": "custom"}\'')
    
    # 解析命令行参数
    args = parser.parse_args()
    
    try:
        # 验证并规范化目录路径
        directory = validate_directory(args.directory)
        logger.info(f"使用目录: {directory}")
        
        # 如果没有指定名称，使用目录名
        base_name = args.name if args.name else os.path.basename(directory)
        
        process_directory(
            base_url=args.base_url,
            cookie=args.cookie,
            authorization=args.authorization,
            directory=directory,
            kb_id=args.kb_id,
            base_name=base_name,
            batch_size=args.batch_size,
            meta=args.meta
        )
    except Exception as e:
        logger.error(f"程序执行出错: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()

# 使用示例：
# 1. 使用当前目录，自动使用目录名作为数据源名称
# python index_user_documents.py \
#     --base-url "http://localhost:8000/api/v1" \
#     --cookie "bid=01971b27-9f4c-7c4d-b492-9cbc8ecca543; session=J56plm9_Zr64Cka9nUiG2kNKAuxK7ApfMHeYepB14Gc" \
#     --directory "." \
#     --kb-id 240001
#
# 2. 使用指定目录，自定义数据源名称
# python index_user_documents.py \
#     --base-url "http://localhost:8000/api/v1" \
#     --cookie "bid=01971b27-9f4c-7c4d-b492-9cbc8ecca543; session=J56plm9_Zr64Cka9nUiG2kNKAuxK7ApfMHeYepB14Gc" \
#     --directory "./test-files" \
#     --kb-id 240001 \
#     --name "自定义名称" \
#     --batch-size 5
#
# 3. 使用上级目录
# python index_user_documents.py \
#     --base-url "http://localhost:8000/api/v1" \
#     --cookie "bid=01971b27-9f4c-7c4d-b492-9cbc8ecca543; session=J56plm9_Zr64Cka9nUiG2kNKAuxK7ApfMHeYepB14Gc" \
#     --directory ".." \
#     --kb-id 240001
#
# 4. 使用 Bearer Token 认证
# python index_user_documents.py \
#     --base-url "http://localhost:8000/api/v1" \
#     --authorization "ta-fAgSH9BFz25KGbWPLv2rX6i7gQbC60VKmsYpSj3EaaylwxebBr" \
#     --directory "/minio/文件资料/行业分析报告" \
#     --kb-id 1 \
#     --batch-size 5
#
# 5. 使用自定义 meta 信息
# python index_user_documents.py \
#     --base-url "http://localhost:8000/api/v1" \
#     --cookie "bid=01971b27-9f4c-7c4d-b492-9cbc8ecca543; session=J56plm9_Zr64Cka9nUiG2kNKAuxK7ApfMHeYepB14Gc" \
#     --directory "./test-files" \
#     --kb-id 240001 \
#     --meta '{"category": "custom", "tags": ["重要", "内部"]}'
#
# 支持的文件类型：
# - .txt (纯文本)
# - .md, .markdown (Markdown)
# - .pdf (PDF)
# - .docx (Word)
# - .pptx (PowerPoint)
# - .xlsx (Excel)
# - .csv (CSV) 