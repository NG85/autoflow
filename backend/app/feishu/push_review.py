import logging
from app.feishu.common_open import (
    DEFAULT_INTERNAL_ADMINS,
    DEFAULT_INTERNAL_SALES,
    HOST,
    get_tenant_access_token,
    send_feishu_message
)
from app.core.config import settings
from collections import defaultdict

logger = logging.getLogger(__name__)

DEFAULT_EXTERNAL_GROUP_CHATS = [
    {
        "name": "集结号（通用）",
        "chat_id": "oc_f9c65db7f9e9b9e93b2ab1ae4d94fddd"
    }
]

DEFAULT_EXTERNAL_LEADERS = [
    {
        "name": "韩启微",
        "email": "qiwei.han@pingcap.cn",
        "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff"
    }
]

DEFAULT_EXTERNAL_SALES = [
    # {
    #     "name": "钟艳珍",
    #     "email": "yanzhen.zhong@pingcap.cn",
    #     "open_id": "ou_0b26ba63639e89dde8d40d154c2b44b8",
    #     "accounts": []
    # },
    {
        "name": "肖章博",
        "email": "zhangbo.xiao@pingcap.cn",
        "open_id": "ou_8cc1d496c513d8202997c8218cc3446c",
        "accounts": []
    },
    {
        "name": "金豫玮",
        "email": "yuwei.jin@pingcap.cn",
        "open_id": "ou_c53c2f904af4531eff0dba69a76c44a1",
        "accounts": []
    },
    {
        "name": "姚亮",
        "email": "liang.yao@pingcap.cn",
        "open_id": "ou_47fc28d13dd04718bbe65a3c0b010d29",
        "accounts": []
    },
    {
        "name": "韩启微",
        "email": "qiwei.han@pingcap.cn",
        "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff",
        "accounts": []
    }
]

def push_account_review(items, review_date, leaders, sales, external=False):
    token = get_tenant_access_token(external=external)
    if not leaders:
        leaders = DEFAULT_EXTERNAL_LEADERS if external else DEFAULT_INTERNAL_ADMINS
    if not sales:
        sales = DEFAULT_EXTERNAL_SALES if external else DEFAULT_INTERNAL_SALES
    
    # 1. 发送account review表给sales，并构建 owner_accounts 映射（去重 execution_id）
    owner_accounts = {}
    seen_execution_ids = set()
    # 先收集所有leader的open_id和email，便于判断
    leader_ids = set()
    for leader in leaders:
        if leader.get("open_id"):
            leader_ids.add(("open_id", leader["open_id"]))
        if leader.get("email"):
            leader_ids.add(("email", leader["email"]))
    for user in sales:
        user_accounts = []
        for item in items:
            if item.get("owner") == user.get("name"):
                execution_id = item.get("execution_id")
                if execution_id and execution_id not in seen_execution_ids:
                    user_accounts.append(item)
                    seen_execution_ids.add(execution_id)
        if user_accounts:
            owner_accounts[user["name"]] = user_accounts
        # 如果user也是leader，则跳过，不发个人消息
        is_leader = False
        if user.get("open_id") and ("open_id", user["open_id"]) in leader_ids:
            is_leader = True
        elif user.get("email") and ("email", user["email"]) in leader_ids:
            is_leader = True
        if is_leader:
            continue
        receive_id = user.get("open_id")
        receive_id_type = "open_id"
        if not receive_id:
            if user.get("email"):
                receive_id = user["email"]
                receive_id_type = "email"
            else:
                logger.warning(f"用户 {user['name']} 没有可用的open_id/email，无法发送")
                continue
        account_lines = [
            f"- [{acc['account_name']}]({HOST}/review/detail/{acc['execution_id']}?accountId={acc['account_id']})"
            for acc in user_accounts
        ]
        accounts_text = "\n".join(account_lines)
        text = (
            f"Sia帮您生成了【account review（{review_date}）】，请查收：\n"
            f"{accounts_text}"
        )
        resp = send_feishu_message(receive_id, token, text, receive_id_type)
        logger.info(f"发送给 {user['name']}的account review表 结果: {resp}")

    # 2. 汇总 owner_accounts 给 leader
    leader_report_lines = []
    for owner, owner_items in owner_accounts.items():
        leader_report_lines.append(f"{owner}:")
        for acc in owner_items:
            url = f"{HOST}/review/detail/{acc['execution_id']}?accountId={acc['account_id']}"
            leader_report_lines.append(f"- [{acc['account_name']}]({url})")
        leader_report_lines.append("")  # 空行分隔

    leader_text = (
        f"Sia帮您生成了【account review（{review_date}）】，请查收：\n"
        + "\n".join(leader_report_lines)
    )

    for leader in leaders:
        receive_id = leader.get("open_id")
        receive_id_type = "open_id"
        if not receive_id:
            if leader.get("email"):
                receive_id = leader["email"]
                receive_id_type = "email"
            else:
                print(f"Leader {leader['name']} 没有可用的open_id/email，无法发送")
                continue
        resp = send_feishu_message(receive_id, token, leader_text, receive_id_type)
        logger.info(f"发送给 {leader['name']}的account review总表 结果: {resp}") 