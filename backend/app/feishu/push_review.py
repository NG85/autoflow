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

DEFAULT_EXTERNAL_LEADERS = [
    {
        "name": "韩启微",
        "email": "qiwei.han@pingcap.cn",
        "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff"
    }
]

DEFAULT_EXTERNAL_SALES = [
    {
        "name": "钟艳珍",
        "email": "yanzhen.zhong@pingcap.cn",
        "open_id": "ou_0b26ba63639e89dde8d40d154c2b44b8",
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
    
    # 1. 发送account review表给sales
    if not external:
        # 平均分配items给sales
        num_sales = len(sales)
        num_items = len(items)
        if num_sales == 0 or num_items == 0:
            return
        import math
        chunk_size = math.ceil(num_items / num_sales)
        for idx, user in enumerate(sales):
            start = idx * chunk_size
            end = min(start + chunk_size, num_items)
            user_accounts = items[start:end]
            if not user_accounts:
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
            # 组装每个account的review链接，格式为 [客户名称](url)
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
    else:
        for user in sales:
            # 根据items中的owner字段分配account
            user_accounts = [item for item in items if item.get("owner") == user.get("name")]
            if not user_accounts:
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

    # 2. 汇总所有items的account review给leader
    leader_report_lines = []
    grouped = defaultdict(list)
    for item in items:
        grouped[item.get("owner", "")] .append(item)
    for owner, owner_items in grouped.items():
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