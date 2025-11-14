from typing import Optional, Tuple, Dict, Any
import logging
import requests
import json
import re
import uuid
from app.core.config import settings
from app.platforms.base.base_client import BaseClient, UnsupportedDocumentTypeError, log_content_preview

logger = logging.getLogger(__name__)


class DingTalkClient(BaseClient):
    """钉钉客户端实现"""
    
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        super().__init__(
            app_id or settings.DINGTALK_APP_ID,
            app_secret or settings.DINGTALK_APP_SECRET
        )
    
    @property
    def base_url(self) -> str:
        return "https://api.dingtalk.com"
    
    @property
    def auth_url(self) -> str:
        return "https://login.dingtalk.com/oauth2/auth"
    
    @property
    def token_url(self) -> str:
        return f"{self.base_url}/v1.0/oauth2/accessToken"
    
    @property
    def platform_name(self) -> str:
        return "dingtalk"
    
    def get_tenant_access_token(self, app_id: Optional[str] = None, app_secret: Optional[str] = None) -> str:
        """获取钉钉access_token - 钉钉特有实现"""        
        # 如果没有指定app_id，使用当前配置的应用ID
        if app_id is None:
            app_id = self.app_id
            app_secret = self.app_secret
        
        headers = {
            "Content-Type": "application/json"
        }
        
        data = {
            "appKey": app_id,
            "appSecret": app_secret
        }
        
        resp = requests.post(self.token_url, headers=headers, json=data)
        resp.raise_for_status()
        
        result = resp.json()
        return result["accessToken"]
    
    def get_supported_document_types(self) -> set:
        """钉钉支持的文档类型（基于category）"""
        return {
            'alidoc',      # 钉钉文档（ALIDOC）- 包括文档、表格、多维表格等
        }
    
    def get_unsupported_document_types(self) -> set:
        """钉钉暂不支持但可识别的文档类型（基于category）"""
        return {
            'document',    # 本地文档（DOCUMENT）
            'image',       # 图片（IMAGE）
            'video',       # 视频（VIDEO）
            'audio',       # 音频（AUDIO）
            'archive',     # 归档文件（ARCHIVE）
            'other',       # 其他类型（OTHER）
        }
    
    def parse_url(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        解析钉钉文档URL
        
        钉钉支持通过完整URL获取节点信息，不需要手动解析node_id
        返回 (url_type, url) - 保留完整URL用于后续API调用
        """
        # 钉钉文档URL格式
        if 'alidocs.dingtalk.com' in url or 'dingtalk.com' in url:
            # 统一返回 alidoc 类型，保留完整URL
            return "alidoc", url
        
        return None, None
    
    def get_content_from_source_with_token(self, url: str, access_token: str) -> Tuple[Optional[str], Optional[str]]:
        """从钉钉源获取内容"""
        url_type, doc_url = self.parse_url(url)
        if not url_type or not doc_url:
            logger.error(f"无法解析钉钉URL: {url}")
            return None, None
        
        logger.info(f"开始获取钉钉内容，URL类型: {url_type}")
        
        try:
            # 对于alidoc类型，先通过URL获取节点信息，再获取内容
            if url_type == 'alidoc':
                doc_type, content = self._get_alidoc_content_by_url(doc_url, access_token)
                if not content:
                    logger.error(f"无法获取钉钉文档内容: {url}")
                    return None, None
                
                logger.info(f"获取到钉钉{doc_type}内容，{log_content_preview(content, prefix='内容预览')}")
                return content, f"dingtalk_{doc_type}"
            else:
                logger.error(f"不支持的钉钉URL类型: {url_type}")
                return None, None
            
        except UnsupportedDocumentTypeError:
            raise
        except Exception as e:
            logger.error(f"获取钉钉内容失败: {e}")
            return None, None
    
    def _get_alidoc_content_by_url(self, url: str, access_token: str) -> Tuple[Optional[str], Optional[str]]:
        """
        通过URL获取阿里文档内容（统一入口）
        
        使用钉钉官方API：通过链接获取节点信息
        参考：https://open.dingtalk.com/document/isvapp/get-node-by-link
        
        Returns:
            (doc_type, content) - 文档类型和内容
        """
        # 1. 通过URL获取节点信息
        node_info = self._get_node_info_by_url(url, access_token)
        if not node_info:
            logger.error(f"无法通过URL获取节点信息: {url}")
            return None, None
        
        # 2. 提取node_id和其他信息
        node_id = node_info.get('nodeId')
        if not node_id:
            logger.error(f"节点信息中缺少nodeId: {node_info}")
            return None, None
        
        category = node_info.get('category', '').upper()  # ALIDOC, DOCUMENT, IMAGE等
        node_type = node_info.get('type', '').upper()     # FILE, FOLDER
        
        logger.info(f"钉钉节点信息 - nodeId: {node_id}, category: {category}, type: {node_type}")
        
        # 3. 只处理文件类型，文件夹不处理
        if node_type == 'FOLDER':
            logger.warning(f"节点是文件夹，不支持获取内容: {node_id}")
            raise UnsupportedDocumentTypeError(f"不支持获取文件夹内容")
        
        # 4. 根据category判断是否支持
        category_lower = category.lower()
        
        # 检查是否支持
        if category_lower not in self.get_supported_document_types():
            if category_lower in self.get_unsupported_document_types():
                type_names = {
                    'document': '本地文档',
                    'image': '图片',
                    'video': '视频',
                    'audio': '音频',
                    'archive': '归档文件',
                    'other': '其他类型'
                }
                type_name = type_names.get(category_lower, category)
                raise UnsupportedDocumentTypeError(f"暂不支持{type_name}内容获取")
            else:
                logger.warning(f"未知的钉钉文档类别: {category}")
        
        # 5. 对于ALIDOC类型，获取具体的文档子类型（doc/sheet/bitable）
        if category == 'ALIDOC':
            # ALIDOC包含多种子类型，需要进一步判断
            # 优先使用extension字段
            extension = node_info.get('extension', '').lower()
            
            # 根据extension判断文档类型
            # extension可能的值：adoc(文档), asheet(表格), atable(多维表格)
            if extension in ['adoc']:
                # 钉钉文档
                content = self._get_document_content(node_id, access_token)
                return 'doc', content
            else:
                # 默认作为文档处理
                logger.info(f"ALIDOC子类型未知(extension: {extension})，默认作为文档处理")
                content = self._get_document_content(node_id, access_token)
                return 'doc', content
        else:
            # 其他类型暂不支持
            raise UnsupportedDocumentTypeError(f"不支持的文档类别: {category}")
    
    def _get_node_info_by_url(self, url: str, access_token: str) -> Optional[Dict[str, Any]]:
        """
        通过URL获取节点信息
        
        使用钉钉官方API：通过链接获取节点
        API文档：https://open.dingtalk.com/document/isvapp/get-node-by-link
        
        请求示例：
        POST /v2.0/wiki/nodes/queryByUrl
        {
            "url": "https://alidocs.dingtalk.com/i/nodes/xxx",
            "option": {
                "withPermissionRole": true,
                "withStatisticalInfo": false
            }
        }
        
        返回示例：
        {
            "node": {
                "nodeId": "xxx",
                "workspaceId": "xxx",
                "name": "node_name",
                "type": "FILE",
                "category": "ALIDOC",
                "extension": "adoc",
                ...
            }
        }
        """
        api_url = f"{self.base_url}/v2.0/wiki/nodes/queryByUrl"
        headers = {"x-acs-dingtalk-access-token": access_token}
        data = {
            "url": url,
            "option": {
                "withPermissionRole": False,  # 不需要权限信息
                "withStatisticalInfo": False   # 不需要统计信息
            }
        }
        
        try:
            resp = requests.post(api_url, headers=headers, json=data)
            resp.raise_for_status()
            
            result = resp.json()
            
            # 钉钉API返回的数据在 "node" 字段中
            node_data = result.get('node')
            if node_data:
                logger.info(f"成功通过URL获取节点信息 - nodeId: {node_data.get('nodeId')}, "
                          f"category: {node_data.get('category')}, type: {node_data.get('type')}")
                return node_data
            else:
                logger.error(f"响应中缺少node字段: {result}")
                return None
        except Exception as e:
            logger.error(f"通过URL获取节点信息异常: {e}")
            return None
    
    def _get_node_info(self, node_id: str, access_token: str) -> Optional[Dict[str, Any]]:
        """获取节点元信息"""
        # 钉钉获取知识库节点信息
        pass
    
    def _get_document_content(self, node_id: str, access_token: str) -> Optional[str]:
        """获取钉钉文档内容"""
        # 钉钉文档API
        pass
    
    def _get_sheet_content(self, node_id: str, access_token: str) -> Optional[str]:
        """获取钉钉表格内容"""
        pass
    
    def _get_bitable_content(self, node_id: str, access_token: str) -> Optional[str]:
        """获取钉钉多维表格内容"""
        pass

    def send_message(self, receive_id: str, token: str, text: str, receive_id_type: str = "open_id", msg_type: str = "text") -> Dict[str, Any]:
        """
        发送钉钉消息
        
        支持两种消息类型：
        1. 文本消息：使用批量发送单聊消息API
           API文档：https://open.dingtalk.com/document/isvapp/send-single-chat-messages-in-bulk
        2. 交互式卡片：使用机器人发送交互式卡片API  
           API文档：https://open.dingtalk.com/document/orgapp/robots-send-interactive-cards
        
        Args:
            receive_id: 接收者ID（用户ID或群聊ID）
            token: 访问令牌（access_token）
            text: 消息内容（文本消息为文本字符串，卡片消息为JSON字符串）
            receive_id_type: 接收者类型（"chat_id"为群聊，其他为单聊）
            msg_type: 消息类型（"text", "interactive"等）
        
        Returns:
            发送结果
        """
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json"
        }
        
        # 根据消息类型选择不同的API
        if msg_type == "interactive":
            # 发送交互式卡片
            return self._send_interactive_card(receive_id_type, receive_id, text, headers)
        elif msg_type == "text":
            # 发送文本消息
            return self._send_text_message(receive_id_type, receive_id, text, headers)
        else:
            return {"errcode": -1, "errmsg": f"Unsupported message type: {msg_type}"}
    
    def _send_text_message(self, receive_id_type: str, receive_id: str, text: str, headers: Dict[str, str]) -> Dict[str, Any]:
        """
        发送文本消息（批量发送单聊消息）
        
        API文档：https://open.dingtalk.com/document/isvapp/send-single-chat-messages-in-bulk
        """
        url = f"{self.base_url}/v1.0/im/conversations/messages/batchSend"
        
        msg_param = {
            "content": text
        }
        
        payload = {
            "robotCode": self.app_id,  # 机器人code
            "userIds": [receive_id],   # 用户ID列表
            "msgKey": "sampleText",  # 消息模板key
            "msgParam": json.dumps(msg_param, ensure_ascii=False)  # 消息参数
        }
        
        if receive_id_type == "chat_id":
            url = f"{self.base_url}/v1.0/robot/groupMessages/send"
            payload={
                "robotCode": self.app_id,  # 机器人code
                "openConversationId": receive_id,   # 群聊ID
                "msgKey": "sampleText",  # 消息模板key
                "msgParam": json.dumps(msg_param, ensure_ascii=False)  # 消息参数
            }
            
        try:
            resp = requests.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            
            result = resp.json()
            
            # 新版API返回格式：包含processQueryKey或invalidStaffIdList
            if "processQueryKey" in result or "invalidStaffIdList" in result:
                logger.info(f"成功发送钉钉文本消息到用户: {receive_id}")
                return {"errcode": 0, "errmsg": "ok", "result": result}
            else:
                logger.error(f"发送钉钉文本消息失败: {result}")
                return result
        except Exception as e:
            logger.error(f"发送钉钉文本消息异常: {e}")
            return {"errcode": -1, "errmsg": str(e)}
    
    def _send_interactive_card(self, receive_id_type: str, receive_id: str, card_data: str, headers: Dict[str, str]) -> Dict[str, Any]:
        """
        发送交互式卡片消息
        
        API文档：https://open.dingtalk.com/document/orgapp/robots-send-interactive-cards
        API端点：POST /v1.0/im/v1.0/robot/interactiveCards/send
        
        Args:
            receive_id_type: 接收者类型（"chat_id"为群聊，其他为单聊）
            receive_id: 接收者ID（群聊ID或用户ID）
            card_data: 卡片数据（JSON字符串），支持两种格式：
                1. 钉钉API格式（直接）：
                   - cardTemplateId: 卡片模板ID（必填）
                   - cardData: 卡片数据（必填）
                   - cardBizId: 卡片业务ID，唯一标识卡片的幂等ID（必填，未提供则自动生成）
                   - callbackUrl: 回调URL（可选）
                   - openConversationId: 会话ID，用于群聊（可选）
                   - singleChatReceiver: 单聊接收者，JSON字符串格式 {"userId":"xxx"} 或直接传userId字符串（可选，默认使用receive_id）
                   - sendOptions: 发送选项（可选）
                   - pullStrategy: 是否使用拉取策略（可选）
                2. Service层格式（自动转换）：
                   {
                     "type": "template",
                     "data": {
                       "template_id": "xxx",
                       "template_variable": {...}
                     }
                   }
            headers: 请求头（包含access token）
        
        Returns:
            发送结果
        """
        url = f"{self.base_url}/v1.0/im/v1.0/robot/interactiveCards/send"
        
        try:
            # 解析卡片数据
            card_json = json.loads(card_data) if isinstance(card_data, str) else card_data
            
            # 检查是否为service层格式，需要转换
            if card_json.get("type") == "template" and "data" in card_json:
                # 转换为钉钉API格式
                template_data = card_json["data"]
                card_json = {
                    "cardTemplateId": template_data.get("template_id"),
                    "cardData": template_data.get("template_variable", {})
                }
                logger.info(f"转换service层格式到钉钉API格式: template_id={card_json['cardTemplateId']}")
            
            # 构建请求负载 - 必填字段
            payload = {
                "robotCode": self.app_id,  # 机器人code
            }
            
            # 必填：卡片模板ID
            if "cardTemplateId" not in card_json:
                return {"errcode": -1, "errmsg": "Missing required field: cardTemplateId"}
            payload["cardTemplateId"] = card_json["cardTemplateId"]
            
            # 必填：卡片数据
            if "cardData" in card_json:
                # cardData需要是JSON字符串
                payload["cardData"] = json.dumps(card_json["cardData"], ensure_ascii=False) if isinstance(card_json["cardData"], dict) else card_json["cardData"]
            else:
                return {"errcode": -1, "errmsg": "Missing required field: cardData"}
            
            # 必填：卡片业务ID（唯一标识，卡片幂等ID）
            # 如果未提供，自动生成UUID（最长不超过100字符，建议64字符以内）
            if "cardBizId" in card_json:
                payload["cardBizId"] = card_json["cardBizId"]
            else:
                # 生成唯一的cardBizId: 使用UUID去掉中划线，保证在64字符以内
                payload["cardBizId"] = f"card_{uuid.uuid4().hex}"
                logger.info(f"自动生成cardBizId: {payload['cardBizId']}")
            
            # 可选：回调URL
            if "callbackUrl" in card_json:
                payload["callbackUrl"] = card_json["callbackUrl"]
            
            # 发送目标：优先使用openConversationId（群聊），否则使用singleChatReceiver（单聊）
            if "openConversationId" in card_json:
                payload["openConversationId"] = card_json["openConversationId"]
            elif "singleChatReceiver" in card_json:
                # singleChatReceiver应该是JSON字符串格式: {"userId":"xxx"}
                receiver = card_json["singleChatReceiver"]
                if isinstance(receiver, str):
                    # 如果已经是字符串，检查是否为JSON格式
                    try:
                        json.loads(receiver)  # 验证是否为有效JSON
                        payload["singleChatReceiver"] = receiver
                    except json.JSONDecodeError:
                        # 如果不是JSON格式，假定是userId，转换为JSON格式
                        payload["singleChatReceiver"] = json.dumps({"userId": receiver}, ensure_ascii=False)
                elif isinstance(receiver, dict):
                    # 如果是字典，转换为JSON字符串
                    payload["singleChatReceiver"] = json.dumps(receiver, ensure_ascii=False)
                else:
                    return {"errcode": -1, "errmsg": "Invalid singleChatReceiver format"}
            else:
                # 根据receive_id_type判断是群聊还是单聊
                if not receive_id:
                    return {"errcode": -1, "errmsg": "Missing receiver: openConversationId or singleChatReceiver required"}
                
                if receive_id_type == "chat_id":
                    # 群聊：使用openConversationId
                    payload["openConversationId"] = receive_id
                else:
                    # 单聊：使用singleChatReceiver
                    # 转换为JSON格式: {"userId":"xxx"}
                    payload["singleChatReceiver"] = json.dumps({"userId": receive_id}, ensure_ascii=False)
            
            # 可选：用户私有数据映射
            if "userIdPrivateDataMap" in card_json:
                payload["userIdPrivateDataMap"] = card_json["userIdPrivateDataMap"]
            if "unionIdPrivateDataMap" in card_json:
                payload["unionIdPrivateDataMap"] = card_json["unionIdPrivateDataMap"]
            
            # 可选：发送选项
            if "sendOptions" in card_json:
                payload["sendOptions"] = card_json["sendOptions"]
            
            # 可选：拉取策略
            if "pullStrategy" in card_json:
                payload["pullStrategy"] = card_json["pullStrategy"]
            
            resp = requests.post(url, headers=headers, json=payload)
            logger.info(f"发送钉钉交互式卡片请求: {resp.text}")
            resp.raise_for_status()
            
            result = resp.json()
            
            # 检查返回结果
            if result.get("success") or "processQueryKey" in result:
                logger.info(f"成功发送钉钉交互式卡片到用户: {receive_id}")
                return {"errcode": 0, "errmsg": "ok", "result": result}
            else:
                logger.error(f"发送钉钉交互式卡片失败: {result}")
                return result
        except json.JSONDecodeError as e:
            logger.error(f"解析卡片数据失败: {e}")
            return {"errcode": -1, "errmsg": f"Invalid card data format: {str(e)}"}
        except Exception as e:
            logger.error(f"发送钉钉交互式卡片异常: {e}")
            return {"errcode": -1, "errmsg": str(e)}

    def query_conference_info_by_room_code(
        self,
        room_code: str,
        access_token: str
    ) -> Optional[Dict[str, Any]]:
        """
        根据会议号查询会议信息，获取会议ID
        
        API文档：https://open.dingtalk.com/document/development/api-queryconferenceinfobyroomcode
        API端点：GET /v1.0/conference/roomCodes/{roomCode}/infos
        
        Args:
            room_code: 会议号
            access_token: 访问令牌

        Returns:
            会议信息，包含conferenceId等字段
        """
        api_url = f"{self.base_url}/v1.0/conference/roomCodes/{room_code}/infos"
        headers = {
            "x-acs-dingtalk-access-token": access_token,
            "Content-Type": "application/json"
        }        
        
        try:
            resp = requests.get(api_url, headers=headers)
            logger.info(f"通过会议号获取会议信息响应: {resp.text}")
            resp.raise_for_status()
            
            result = resp.json()
            logger.info(f"成功通过会议号获取会议信息: roomCode={room_code}")
            return result
        except Exception as e:
            logger.error(f"通过会议号获取会议信息异常: {e}")
            return None
    
    def query_cloud_recording_text(
        self,
        conference_id: str,
        union_id: str,
        access_token: str,
        start_time: Optional[int] = None,
        direction: Optional[str] = "0",
        max_results: Optional[int] = 2000,
        next_token: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        查询会议录制的文本信息
        
        API文档：https://open.dingtalk.com/document/development/queries-the-text-information-about-cloud-recording
        API端点：GET /v1.0/conference/videoConferences/{conferenceId}/cloudRecords/getTexts
        
        Args:
            conference_id: 会议ID（从query_conference_info_by_room_code获取）
            union_id: 会议发起人的unionId
            access_token: 访问令牌
            start_time: 开始时间戳（可选，Long类型）
            direction: 查询方式，0（时间由小到大）或1（时间由大到小），默认0
            max_results: 单次查询条数，最大2000
            next_token: 分页令牌，第一次查询时为空，后续查询时为上一次查询的next_token
            
        Returns:
            会议录制的文本信息
        """
        api_url = f"{self.base_url}/v1.0/conference/videoConferences/{conference_id}/cloudRecords/getTexts"
        headers = {
            "x-acs-dingtalk-access-token": access_token,
            "Content-Type": "application/json"
        }
        
        # 构建查询参数
        params = {
            "unionId": union_id,
            "direction": direction,
            "maxResults": max_results
        }
        if start_time is not None:
            params["startTime"] = start_time
        if next_token is not None:
            params["nextToken"] = next_token
        
        try:
            resp = requests.get(api_url, headers=headers, params=params)
            resp.raise_for_status()
            
            result = resp.json()
            logger.info(f"成功查询会议录制文本信息: conferenceId={conference_id}, unionId={union_id}")
            return result
        except Exception as e:
            logger.error(f"查询会议录制文本信息异常: {e}")
            return None

# 创建默认的钉钉客户端实例
dingtalk_client = DingTalkClient()
