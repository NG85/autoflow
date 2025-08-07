import logging
import os
from typing import Optional
from io import BytesIO

from app.core.config import settings
from app.rag.datasource.file import extract_text_from_docx

logger = logging.getLogger(__name__)


def get_file_content_from_local_storage(file_path: str) -> tuple[Optional[str], Optional[str]]:
    """
    从本地挂载的存储中获取文件内容
    
    Args:
        file_path: 文件路径
        
    Returns:
        tuple: (文件内容, 文档类型) 或 (None, None) 如果获取失败
    """
    try:
        # 构建完整的本地文件路径
        full_path = os.path.join(settings.LOCAL_FILE_STORAGE_PATH, file_path)
        logger.info(f"Reading file from local path: {full_path}")
        
        # 检查文件是否存在
        if not os.path.exists(full_path):
            logger.error(f"File not found: {full_path}")
            return None, None
        
        # 读取文件内容
        with open(full_path, 'rb') as f:
            file_content = f.read()
        
        file_extension = os.path.splitext(file_path)[1].lower()
        
        # 暂时只支持Word文档
        if file_extension == '.docx':
            content = extract_text_from_docx(BytesIO(file_content))
            document_type = 'file_docx'
        else:
            logger.warning(f"Unsupported file type: {file_extension}, only .docx files are supported")
            content = None
            document_type = 'file_unsupported'
        
        logger.info(f"Successfully extracted content from {file_path}, type: {document_type}")
        return content, document_type
        
    except Exception as e:
        logger.error(f"Failed to read file from {file_path}: {e}")
        return None, None
