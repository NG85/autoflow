from typing import Optional, Tuple, Dict, Any
from abc import abstractmethod
import logging
import requests
import json
import re
from .base_client import BaseClient, UnsupportedDocumentTypeError, log_content_preview

logger = logging.getLogger(__name__)


class BaseLarkClient(BaseClient):
    """飞书/Lark客户端基类"""
    
    def __init__(self, app_id: str, app_secret: str):
        super().__init__(app_id, app_secret)

    # 抽象方法 - Lark系列平台必须实现
    @property
    @abstractmethod
    def base_url(self) -> str:
        """API基础URL"""
        pass
    
    @property
    def auth_url(self) -> str:
        return f"{self.base_url}/authen/v1/index"
    
    @property
    def token_url(self) -> str:
        return f"{self.base_url}/auth/v3/tenant_access_token/internal/"
        
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称，用于日志和文档类型"""
        pass
    
    # Lark系列共享的实现方法
    def get_tenant_access_token(self, app_id: Optional[str] = None, app_secret: Optional[str] = None) -> str:
        """获取tenant_access_token - Lark系列实现"""
        url = self.token_url
        
        # 如果没有指定app_id，使用当前配置的应用ID
        if app_id is None:
            app_id = self.app_id
            app_secret = self.app_secret
        
        resp = requests.post(url, json={
            "app_id": app_id,
            "app_secret": app_secret
        })
        resp.raise_for_status()
        return resp.json()["tenant_access_token"]
    
    def get_supported_document_types(self) -> set:
        """Lark系列支持的文档类型"""
        return {
            'doc', 'docx',      # 飞书文档
            'minutes',           # 飞书妙记
            'wiki_node',        # 知识库节点
        }
    
    def get_unsupported_document_types(self) -> set:
        """Lark系列暂不支持但可识别的文档类型"""
        return {
            'sheet',            # 电子表格
            'bitable',          # 多维表格
        }
    
    def parse_url(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """解析URL，返回(url_type, token) - Lark系列实现"""
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
        # 会议纪要
        m = re.match(r"https://[^/]+/minutes/([a-zA-Z0-9]+)", url)
        if m:
            return "minutes", m.group(1)
        # 其他类型
        return None, None
    
    def get_content_from_source_with_token(self, url: str, access_token: str) -> Tuple[Optional[str], Optional[str]]:
        """从源获取内容 - Lark系列实现"""
        url_type, token = self.parse_url(url)
        if not url_type or not token:
            logger.error(f"无法解析URL: {url}")
            return None, None
        
        logger.info(f"开始获取{self.platform_name}内容，类型: {url_type}, token: {token}")
        
        try:
            # 检查文档类型是否支持
            self.check_document_type_support(url_type, url)
            
            # 根据URL类型获取内容
            content = None
            if url_type in ['doc', 'docx']:
                # 文档类型 - 优先获取markdown格式，失败则获取原始内容
                content = self._get_document_markdown(token, access_token)
                if not content:
                    content = self._get_document_content(token, access_token, url_type)
            elif url_type == 'minutes':
                # 飞书妙记类型
                content = self._get_minutes_content(token, access_token)
            elif url_type == 'wiki_node':
                # 知识库节点类型
                content = self._get_wiki_node_content(token, access_token)
            else:
                logger.error(f"不支持的{self.platform_name}文档类型: {url_type}")
                return None, None
            
            if not content:
                logger.error(f"无法获取内容: {url}")
                return None, url_type
            
            logger.info(f"获取到内容，{log_content_preview(content, prefix='内容预览')}")
            
            # 统一文档类型命名
            if url_type in ['doc', 'docx']:
                document_type = f'{self.platform_name}_doc'
            elif url_type == 'minutes':
                document_type = f'{self.platform_name}_minute'
            else:
                document_type = f'{self.platform_name}_{url_type}'
            
            return content, document_type
            
        except UnsupportedDocumentTypeError:
            # 重新抛出不支持类型异常
            raise
        except Exception as e:
            logger.error(f"获取{self.platform_name}内容失败: {e}")
            return None, None

    def check_document_type_support(self, url_type: str, url: str) -> None:
        """检查文档类型是否支持 - Lark系列重写"""
        if url_type in self.get_unsupported_document_types():
            type_names = {
                'sheet': '电子表格',
                'bitable': '多维表格'
            }
            type_name = type_names.get(url_type, url_type)
            raise UnsupportedDocumentTypeError(f"暂不支持{type_name}内容获取: {url}")
        
        if url_type not in self.get_supported_document_types():
            raise UnsupportedDocumentTypeError(f"不支持的{self.platform_name}内容类型: {url_type}")
    
    def _get_document_markdown(self, token: str, access_token: str) -> Optional[str]:
        """获取文档markdown格式内容"""
        url = f"{self.base_url}/docs/v1/content"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "doc_token": token,
            "doc_type": "docx",
            "content_type": "markdown"
        }
        
        try:
            resp = requests.get(url, headers=headers, params=params)
            resp.raise_for_status()
            
            data = resp.json()
            if data.get("code") == 0:
                content = data.get("data", {}).get("content", "")
                logger.info(f"成功获取{self.platform_name}文档markdown内容: {log_content_preview(content)}")
                return content
            else:
                logger.warning(f"获取{self.platform_name}文档markdown内容失败: {data}")
                return None
        except Exception as e:
            logger.warning(f"获取{self.platform_name}文档markdown内容异常: {e}")
            return None
    
    def _get_document_content(self, token: str, access_token: str, doc_type: str) -> Optional[str]:
        """获取文档原始内容"""
        url = f"{self.base_url}/docx/v1/documents/{token}/raw_content"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        try:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            
            data = resp.json()
            if data.get("code") == 0:
                content = data.get("data", {}).get("content", "")
                logger.info(f"成功获取{self.platform_name}文档原始内容: {log_content_preview(content)}")
                return content
            else:
                logger.error(f"获取{self.platform_name}文档原始内容失败: {data}")
                return None
        except Exception as e:
            logger.error(f"获取{self.platform_name}文档原始内容异常: {e}")
            return None
    
    def _get_wiki_node_content(self, token: str, access_token: str) -> Optional[str]:
        """获取知识库节点内容"""
        # 首先获取节点详情
        node_info = self._get_node_detail(access_token, token)
        if node_info is None:
            logger.error(f"无法获取知识库节点详情: {token}")
            return None
        
        # 从节点信息中获取obj_type和obj_token
        obj_type = None
        obj_token = None
        if "node" in node_info:
            obj_type = node_info["node"]["obj_type"]
            obj_token = node_info["node"]["obj_token"]
        else:
            obj_type = node_info.get("obj_type")
            obj_token = node_info.get("obj_token")
        
        if not obj_token:
            logger.error(f"无法获取知识库节点的obj_token: {token}")
            return None
        
        # 根据obj_type获取内容
        if obj_type == "docx":
            # 知识库节点指向文档，获取文档内容
            content = self._get_document_markdown(obj_token, access_token)
            if not content:
                content = self._get_document_content(obj_token, access_token, "docx")
            return content
        else:
            logger.error(f"知识库节点类型不支持: {obj_type}")
            return None
    
    def _get_node_detail(self, access_token: str, node_token: str, obj_type: str = None) -> Optional[dict]:
        """获取空间节点详细信息"""
        url = f"{self.base_url}/wiki/v2/spaces/get_node"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"token": node_token}
        if obj_type:
            params["obj_type"] = obj_type
        
        try:
            resp = requests.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                logger.error(f"访问节点详细信息失败 {node_token}")
                logger.error(resp.text)
                return None
            
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data")
            else:
                logger.error(f"获取节点详情失败: {data}")
                return None
        except Exception as e:
            logger.error(f"获取节点详情异常: {e}")
            return None
    
    def _get_minutes_content(self, token: str, access_token: str) -> Optional[str]:
        """获取会议纪要内容"""
        url = f"{self.base_url}/minutes/v1/minutes/{token}/transcript"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "need_speaker": "true",      # 包含发言人信息
            "need_timestamp": "true",    # 包含时间戳信息  
            "file_format": "txt"         # 文件格式，可选：txt, srt
        }
        
        try:
            resp = requests.get(url, headers=headers, params=params)
            resp.raise_for_status()
            
            # 处理二进制流响应
            content = resp.content.decode('utf-8')
            
            if content:
                logger.info(f"成功获取{self.platform_name}会议纪要内容: {log_content_preview(content)}")
                return content
            else:
                logger.warning("获取会议纪要内容为空")
                return None
        except Exception as e:
            logger.error(f"获取{self.platform_name}会议纪要内容失败: {e}")
            return None
    
    def _get_sheet_content(self, token: str, access_token: str) -> Optional[str]:
        """获取电子表格内容"""
        url = f"{self.base_url}/sheets/v2/spreadsheets/{token}/values"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        try:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            
            data = resp.json()
            if data.get("code") == 0:
                values = data.get("data", {}).get("valueRange", {}).get("values", [])
                content = json.dumps(values, ensure_ascii=False, indent=2)
                logger.info(f"成功获取{self.platform_name}电子表格内容: {log_content_preview(content)}")
                return content
            else:
                logger.error(f"获取{self.platform_name}电子表格内容失败: {data}")
                return None
        except Exception as e:
            logger.error(f"获取{self.platform_name}电子表格内容异常: {e}")
            return None
    
    def _get_bitable_content(self, token: str, access_token: str) -> Optional[str]:
        """获取多维表格内容"""
        url = f"{self.base_url}/bitable/v1/apps/{token}/tables"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        try:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            
            data = resp.json()
            if data.get("code") == 0:
                tables = data.get("data", {}).get("items", [])
                content = json.dumps(tables, ensure_ascii=False, indent=2)
                logger.info(f"成功获取{self.platform_name}多维表格内容: {log_content_preview(content)}")
                return content
            else:
                logger.error(f"获取{self.platform_name}多维表格内容失败: {data}")
                return None
        except Exception as e:
            logger.error(f"获取{self.platform_name}多维表格内容异常: {e}")
            return None
    
    def send_message(self, receive_id: str, token: str, text: str, receive_id_type: str = "open_id", msg_type: str = "text") -> Dict[str, Any]:
        """发送消息"""
        url = f"{self.base_url}/im/v1/messages"
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
        
        resp = requests.post(url, params=params, headers=headers, data=json.dumps(payload))
        if resp.status_code != 200:
            logger.error(f"发送{self.platform_name}消息失败 {resp.text}, content: {log_content_preview(content)}")
        resp.raise_for_status()
        
        result = resp.json()
        logger.info(f"成功发送{self.platform_name}消息: {result}")
        return result
