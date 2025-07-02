import logging
from app.feishu.common_open import (
    DEFAULT_INTERNAL_GROUP_CHATS,
    DEFAULT_INTERNAL_USERS,
    get_tenant_access_token,
    send_feishu_message
)
from sqlalchemy.sql import text

logger = logging.getLogger(__name__)

DEFAULT_EXTERNAL_GROUP_CHATS = [
    # {
    #     "name": "",
    #     "chat_id": ""
    # }
]

def get_external_users(session):
    sql = "SELECT u.name, u.email, u.open_id FROM `user` AS u WHERE u.open_id IS NOT NULL AND u.name IS NOT NULL AND u.delete_flag = 0"
    result = session.execute(text(sql))
    return [dict(row._mapping) for row in result]

def push_release_notes(db_session, notes, notes_type, external=False):
    token = get_tenant_access_token(external=external)
    if external:
        users = get_external_users(db_session)
    else:
        users = DEFAULT_INTERNAL_USERS
    
    # 发送release notes
    for user in users:
        receive_id = user.get("open_id")
        receive_id_type = "open_id"
        if not receive_id:
            if user.get("email"):
                receive_id = user["email"]
                receive_id_type = "email"
            else:
                logger.warning(f"用户 {user['name']} 没有可用的open_id/email，无法发送")
                continue
        resp = send_feishu_message(receive_id, token, notes, receive_id_type, notes_type)
        logger.info(f"发送给 {user['name']} Release Notes 结果: {resp}")
    
    group_chats = DEFAULT_EXTERNAL_GROUP_CHATS if external else DEFAULT_INTERNAL_GROUP_CHATS
    for group_chat in group_chats:
        resp = send_feishu_message(group_chat["chat_id"], token, notes, "chat_id", notes_type)
        logger.info(f"发送给 {group_chat['name']} Release Notes 结果: {resp}")
