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

    def send_message(
        self,
        receive_id: str,
        token: str,
        text: str,
        receive_id_type: str = "open_id",
        msg_type: str = "text",
        robot_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        发送钉钉消息
        
        支持两种消息类型：
        1. 文本消息：使用批量发送单聊消息API
           API文档：https://open.dingtalk.com/document/isvapp/send-single-chat-messages-in-bulk
        2. 交互式卡片：使用创建并投放卡片API  
           API文档：https://open.dingtalk.com/document/development/create-and-deliver-cards
        
        Args:
            receive_id: 接收者ID（用户ID或群聊ID）
            token: 访问令牌（access_token）
            text: 消息内容（文本消息为文本字符串，卡片消息为JSON字符串）
            receive_id_type: 接收者类型（"chat_id"为群聊，其他为单聊）
            msg_type: 消息类型（"text", "interactive"等）
            robot_code: 机器人 AppKey（与 token 对应的应用一致；不传则使用当前客户端默认 app_id）
        
        Returns:
            发送结果
        """
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json"
        }
        raw = (robot_code or self.app_id or "").strip()
        effective_robot = raw or (self.app_id or "")
        
        # 根据消息类型选择不同的API
        if msg_type == "interactive":
            # 发送交互式卡片
            return self._send_interactive_card(
                receive_id_type, receive_id, text, headers, robot_code=effective_robot
            )
        elif msg_type == "text":
            # 发送文本消息
            return self._send_text_message(
                receive_id_type, receive_id, text, headers, robot_code=effective_robot
            )
        else:
            return {"errcode": -1, "errmsg": f"Unsupported message type: {msg_type}"}
    
    def _send_text_message(
        self,
        receive_id_type: str,
        receive_id: str,
        text: str,
        headers: Dict[str, str],
        robot_code: str,
    ) -> Dict[str, Any]:
        """
        发送文本消息（批量发送单聊消息）
        
        API文档：https://open.dingtalk.com/document/isvapp/send-single-chat-messages-in-bulk
        """
        url = f"{self.base_url}/v1.0/robot/oToMessages/batchSend"
        
        msg_key = "sampleMarkdown" if "https://" in text else "sampleText"
        msg_param = {
            "content": text
        } if msg_key == "sampleText" else {
            "title": "新的互动提醒",
            "text": text.replace("\n", "\n\n")
        }
        
        payload = {
            "robotCode": robot_code,  # 机器人code
            "userIds": [receive_id],   # 用户ID列表
            "msgKey": msg_key,  # 消息模板key
            "msgParam": json.dumps(msg_param, ensure_ascii=False)  # 消息参数
        }
        
        if receive_id_type == "chat_id":
            url = f"{self.base_url}/v1.0/robot/groupMessages/send"
            payload={
                "robotCode": robot_code,  # 机器人code
                "openConversationId": receive_id,   # 群聊ID
                "msgKey": msg_key,  # 消息模板key
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
    
    def _send_interactive_card(
        self,
        receive_id_type: str,
        receive_id: str,
        card_data: str,
        headers: Dict[str, str],
        robot_code: str,
    ) -> Dict[str, Any]:
        """
        发送交互式卡片消息（新版API - 创建并投放卡片）
        
        API文档：https://open.dingtalk.com/document/development/create-and-deliver-cards
        API端点：POST /v1.0/card/instances/createAndDeliver
        
        场域类型及openSpaceId格式：
            dtv1.card//im_group.{openConversationId}   （IM群聊）
            dtv1.card//im_robot.{userId}               （IM机器人单聊）
        
        Args:
            receive_id_type: 接收者类型（"chat_id"为群聊，其他为单聊）
            receive_id: 接收者ID（群聊openConversationId或用户userId）
            card_data: 卡片数据（JSON字符串），支持两种格式：
                1. 钉钉新版API格式（直接）：
                   - cardTemplateId: 卡片模板ID（必填）
                   - outTrackId: 外部卡片实例ID，唯一标识卡片（未提供则自动生成）
                   - cardData: 卡片数据，格式为 {"cardParamMap": {...}}
                   - userId: 卡片创建者userId（可选）
                   - callbackRouteKey: 卡片回调路由Key（可选）
                   - callbackType: 卡片回调模式，"HTTP" 或 "STREAM"（可选）
                   - openSpaceId: 场域ID（可选，未提供时根据receive_id_type自动构建）
                   - imGroupOpenSpaceModel: IM群聊场域信息（可选）
                   - imRobotOpenSpaceModel: IM机器人单聊场域信息（可选）
                   - imGroupOpenDeliverModel: 群聊投放参数（可选，未提供时自动构建）
                   - imRobotOpenDeliverModel: 单聊投放参数（可选，未提供时自动构建）
                   - privateData: 用户私有数据（可选）
                   - openDynamicDataConfig: 动态数据源配置（可选）
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
        url = f"{self.base_url}/v1.0/card/instances/createAndDeliver"
        
        try:
            card_json = json.loads(card_data) if isinstance(card_data, str) else card_data
            
            # Service层格式 → 钉钉API格式
            if card_json.get("type") == "template" and "data" in card_json:
                template_data = card_json["data"]
                card_json = {
                    "cardTemplateId": template_data.get("template_id"),
                    "cardData": {"cardParamMap": template_data.get("template_variable", {})}
                }
                logger.info(f"转换service层格式到钉钉API格式: template_id={card_json['cardTemplateId']}")
            
            # 兼容旧版cardData格式：确保包装为 {"cardParamMap": {...}}
            if "cardData" in card_json:
                cd = card_json["cardData"]
                if isinstance(cd, str):
                    cd = json.loads(cd)
                if isinstance(cd, dict) and "cardParamMap" not in cd:
                    card_json["cardData"] = {"cardParamMap": cd}
                else:
                    card_json["cardData"] = cd
            
            # cardParamMap 的所有值必须为字符串类型
            param_map = (card_json.get("cardData") or {}).get("cardParamMap")
            if isinstance(param_map, dict):
                card_json["cardData"]["cardParamMap"] = {
                    k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
                    for k, v in param_map.items()
                }
            
            payload: Dict[str, Any] = {}
            
            # 必填：卡片模板ID
            if "cardTemplateId" not in card_json:
                return {"errcode": -1, "errmsg": "Missing required field: cardTemplateId"}
            payload["cardTemplateId"] = card_json["cardTemplateId"]
            
            # 必填：卡片数据
            if "cardData" not in card_json:
                return {"errcode": -1, "errmsg": "Missing required field: cardData"}
            payload["cardData"] = card_json["cardData"]
            
            # 外部卡片实例ID（兼容旧版cardBizId）
            out_track_id = card_json.get("outTrackId") or card_json.get("cardBizId")
            if not out_track_id:
                out_track_id = str(uuid.uuid4())
                logger.info(f"自动生成outTrackId: {out_track_id}")
            payload["outTrackId"] = out_track_id
            
           
            # 可选：卡片回调路由Key
            if "callbackRouteKey" in card_json:
                payload["callbackRouteKey"] = card_json["callbackRouteKey"]
            
            # 可选：卡片回调模式（"HTTP" 或 "STREAM"）
            if "callbackType" in card_json:
                payload["callbackType"] = card_json["callbackType"]
            
            # --- 构建 openSpaceId 和投放模型 ---
            # openSpaceId 格式: dtv1.card//im_group.{openConversationId} 或 dtv1.card//im_robot.{userId}
            if "openSpaceId" in card_json:
                payload["openSpaceId"] = card_json["openSpaceId"]
            else:
                if not receive_id:
                    return {"errcode": -1, "errmsg": "Missing receiver: receive_id is required"}
                
                if receive_id_type == "chat_id":
                    conversation_id = card_json.get("openConversationId", receive_id)
                    payload["openSpaceId"] = f"dtv1.card//im_group.{conversation_id}"
                else:
                    target_user_id = receive_id
                    if "singleChatReceiver" in card_json:
                        receiver = card_json["singleChatReceiver"]
                        if isinstance(receiver, str):
                            try:
                                target_user_id = json.loads(receiver).get("userId", receive_id)
                            except json.JSONDecodeError:
                                target_user_id = receiver
                        elif isinstance(receiver, dict):
                            target_user_id = receiver.get("userId", receive_id)
                    payload["openSpaceId"] = f"dtv1.card//im_robot.{target_user_id}"
            
            # IM群聊投放参数
            group_deliver_model = card_json.get("imGroupOpenDeliverModel") or {}
            if isinstance(group_deliver_model, str):
                try:
                    group_deliver_model = json.loads(group_deliver_model)
                except json.JSONDecodeError:
                    group_deliver_model = {}
            if not isinstance(group_deliver_model, dict):
                group_deliver_model = {}
            group_deliver_model.setdefault("robotCode", robot_code)
            payload["imGroupOpenDeliverModel"] = group_deliver_model
            
            # IM机器人单聊投放参数
            robot_deliver_model = card_json.get("imRobotOpenDeliverModel") or {}
            if isinstance(robot_deliver_model, str):
                try:
                    robot_deliver_model = json.loads(robot_deliver_model)
                except json.JSONDecodeError:
                    robot_deliver_model = {}
            if not isinstance(robot_deliver_model, dict):
                robot_deliver_model = {}
            robot_deliver_model.setdefault("spaceType", "IM_ROBOT")
            robot_deliver_model.setdefault("robotCode", robot_code)
            payload["imRobotOpenDeliverModel"] = robot_deliver_model

            # 场域信息（实测接口要求始终携带）
            group_space = card_json.get("imGroupOpenSpaceModel") or {}
            if isinstance(group_space, dict):
                group_space = dict(group_space)
            else:
                group_space = {}
            group_space["supportForward"] = True
            payload["imGroupOpenSpaceModel"] = group_space

            robot_space = card_json.get("imRobotOpenSpaceModel") or {}
            if isinstance(robot_space, dict):
                robot_space = dict(robot_space)
            else:
                robot_space = {}
            robot_space["supportForward"] = True
            payload["imRobotOpenSpaceModel"] = robot_space

            # 用户私有数据（兼容旧版 userIdPrivateDataMap）
            if "privateData" in card_json:
                payload["privateData"] = card_json["privateData"]
            elif "userIdPrivateDataMap" in card_json:
                payload["privateData"] = card_json["userIdPrivateDataMap"]
            
            # 动态数据源配置
            if "openDynamicDataConfig" in card_json:
                payload["openDynamicDataConfig"] = card_json["openDynamicDataConfig"]
            
            payload["userIdType"] = 1 # 1: userId, 2: unionId
            
            logger.debug(f"钉钉交互式卡片请求payload: {json.dumps(payload, ensure_ascii=False)}")
            resp = requests.post(url, headers=headers, json=payload)
            logger.info(f"发送钉钉交互式卡片响应: {resp.text}")
            resp.raise_for_status()
            
            result = resp.json()

            # 顶层 success 为 True 且 result.deliverResults 中每一项均为 success 时，才视为成功
            top_ok = result.get("success")
            deliver_results = (result.get("result") or {}).get("deliverResults") or []
            all_deliver_ok = all(
                isinstance(r, dict) and r.get("success") is True for r in deliver_results
            )
            call_ok = top_ok and (all_deliver_ok if deliver_results else True)

            if call_ok:
                logger.info(f"成功发送钉钉交互式卡片到用户: {receive_id}")
                return {"errcode": 0, "errmsg": "ok", "result": result.get("result", result)}
            else:
                errmsg = "发送失败"
                for r in deliver_results:
                    if isinstance(r, dict) and r.get("success") is False:
                        errmsg = r.get("errorMsg") or errmsg
                        break
                if not deliver_results and not top_ok:
                    errmsg = result.get("errmsg") or result.get("message") or errmsg
                logger.error(f"发送钉钉交互式卡片失败: {errmsg}, result={result}")
                return {"errcode": -1, "errmsg": errmsg, "result": result.get("result", result)}
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
