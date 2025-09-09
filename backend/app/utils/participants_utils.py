"""
协同参与人字段处理工具函数
统一处理协同参与人字段的解析和格式化逻辑
"""
import json
import logging
from typing import List, Union, Optional, Dict, Any

logger = logging.getLogger(__name__)


def parse_collaborative_participants_names(
    collaborative_participants: Union[str, List, None]
) -> List[str]:
    """
    解析协同参与人字段，提取所有参与者的name
    
    Args:
        collaborative_participants: 协同参与人数据，支持字符串、列表或None
        
    Returns:
        List[str]: 参与者姓名列表
    """
    if not collaborative_participants:
        return []
    
    participant_names = []
    
    if isinstance(collaborative_participants, str):
        # 如果是字符串，尝试解析为JSON
        try:
            parsed = json.loads(collaborative_participants)
            if isinstance(parsed, list):
                # JSON数组格式，提取name字段
                for participant in parsed:
                    if isinstance(participant, dict) and participant.get("name"):
                        participant_names.append(participant["name"])
                    else:
                        # 如果不是标准格式，按原值处理
                        participant_names.append(str(participant))
            else:
                # 非数组格式，按原字符串处理
                participant_names.append(collaborative_participants)
        except (json.JSONDecodeError, TypeError):
            # 解析失败，按原字符串处理
            participant_names.append(collaborative_participants)
    elif isinstance(collaborative_participants, list):
        # 已经是列表格式
        for participant in collaborative_participants:
            if isinstance(participant, dict) and participant.get("name"):
                participant_names.append(participant["name"])
            else:
                participant_names.append(str(participant))
    
    return participant_names


def format_collaborative_participants_names(
    collaborative_participants: Union[str, List, None],
    separator: str = ", "
) -> Optional[str]:
    """
    解析协同参与人字段并格式化为拼接的姓名字符串
    
    Args:
        collaborative_participants: 协同参与人数据
        separator: 分隔符，默认为", "
        
    Returns:
        Optional[str]: 拼接的姓名字符串，如果没有参与者则返回None
    """
    participant_names = parse_collaborative_participants_names(collaborative_participants)
    return separator.join(participant_names) if participant_names else None


def parse_collaborative_participants_list(
    collaborative_participants: Union[str, List, None]
) -> List[Dict[str, Any]]:
    """
    解析协同参与人字段为结构化列表，用于需要访问ask_id等字段的场景
    
    Args:
        collaborative_participants: 协同参与人数据
        
    Returns:
        List[Dict[str, Any]]: 参与者字典列表，每个字典包含name和ask_id字段
    """
    if not collaborative_participants:
        return []
    
    participants = []
    
    if isinstance(collaborative_participants, str):
        # 如果是字符串，尝试解析为JSON
        try:
            parsed = json.loads(collaborative_participants)
            if isinstance(parsed, list):
                # JSON数组格式
                for participant in parsed:
                    if isinstance(participant, dict):
                        participants.append(participant)
                    else:
                        # 如果不是字典格式，转换为字典
                        participants.append({"name": str(participant), "ask_id": None})
            else:
                # 非数组格式，按原字符串处理
                participants.append({"name": collaborative_participants, "ask_id": None})
        except (json.JSONDecodeError, TypeError):
            # 解析失败，按原字符串处理
            participants.append({"name": collaborative_participants, "ask_id": None})
    elif isinstance(collaborative_participants, list):
        # 已经是列表格式
        for participant in collaborative_participants:
            if isinstance(participant, dict):
                participants.append(participant)
            else:
                participants.append({"name": str(participant), "ask_id": None})
    
    return participants


def validate_collaborative_participants_format(
    collaborative_participants: Union[str, List, None]
) -> bool:
    """
    验证协同参与人字段格式是否正确
    
    Args:
        collaborative_participants: 协同参与人数据
        
    Returns:
        bool: 格式是否正确
    """
    if not collaborative_participants:
        return True
    
    if isinstance(collaborative_participants, list):
        return True
    
    if isinstance(collaborative_participants, str):
        try:
            parsed = json.loads(collaborative_participants)
            return isinstance(parsed, list)
        except (json.JSONDecodeError, TypeError):
            # 不是JSON格式，但字符串格式也是有效的（向后兼容）
            return True