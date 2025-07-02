import logging
from app.feishu.common_open import (
    DEFAULT_INTERNAL_ADMINS,
    HOST,
    get_tenant_access_token,
    send_feishu_message
)
from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_LEGACY_ADMINS = [
    {
        "name": "崔秋",
        "email": "cuiqiu@pingcap.cn",
        "open_id": "ou_718d03819e549537c4dc972154798a81"
    },
    {
        "name": "余梦杰",
        "email": "jason.yu@pingcap.cn", 
        "open_id": "ou_f750a3628dc15388a198c643ce786910"
    },
    {
        "name": "龙恒",
        "email": "ls@pingcap.cn",
        "open_id": "ou_adcaafc471d57fc6f9b209c05c0f5ce1"
    },
    {
        "name": "林微",
        "email": "wei.lin@pingcap.cn",
        "open_id": "ou_edbdc2e3fc8eb411bbc49cc586629709"
    }
]

DEFAULT_EXTERNAL_ADMINS = [
    {
        "name": "韩启微",
        "email": "qiwei.han@pingcap.cn",
        "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff"
    },
    {
        "name": "钟艳珍",
        "email": "yanzhen.zhong@pingcap.cn",
        "open_id": "ou_0b26ba63639e89dde8d40d154c2b44b8",
    },
    {
        "name": "金豫玮",
        "email": "yuwei.jin@pingcap.cn",
        "open_id": "ou_c53c2f904af4531eff0dba69a76c44a1",
    },
    {
        "name": "姚亮",
        "email": "liang.yao@pingcap.cn",
        "open_id": "ou_47fc28d13dd04718bbe65a3c0b010d29",
    }
]


def push_weekly_reports(items, receivers, external=False, legacy=False):
    token = get_tenant_access_token(external=external)
    if not receivers:
        if legacy:
            receivers = DEFAULT_LEGACY_ADMINS if external else DEFAULT_INTERNAL_ADMINS
        else:
            receivers = DEFAULT_EXTERNAL_ADMINS if external else DEFAULT_INTERNAL_ADMINS
    
    # 组装消息
    lines = [
        f"Sia帮您生成了[【{item['report_name']}】]({HOST}/review/weeklyDetail/{item['execution_id']})，请点击链接查看详情。"
        for item in items
    ]
    text = '\n'.join(lines)
    
    for receiver in receivers:
        resp = send_feishu_message(receiver["open_id"], token, text)
        logger.info(f"发送给 {receiver['name']} 结果: {resp}")
