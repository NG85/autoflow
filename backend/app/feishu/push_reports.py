import logging
from app.feishu.common_open import (
    DEFAULT_INTERNAL_ADMINS,
    DEFAULT_MERGED_INTERNAL_ADMINS,
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
    # {
    #     "name": "钟艳珍",
    #     "email": "yanzhen.zhong@pingcap.cn",
    #     "open_id": "ou_0b26ba63639e89dde8d40d154c2b44b8",
    # },
    {
        "name": "肖章博",
        "email": "zhangbo.xiao@pingcap.cn",
        "open_id": "ou_8cc1d496c513d8202997c8218cc3446c",
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


DEFAULT_MERGED_ADMINS = [
    {
        "name": "韩启微",
        "email": "qiwei.han@pingcap.cn",
        "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff"
    },
    {
        "name": "肖章博",
        "email": "zhangbo.xiao@pingcap.cn",
        "open_id": "ou_8cc1d496c513d8202997c8218cc3446c"
    },
    {
        "name": "金豫玮",
        "email": "yuwei.jin@pingcap.cn",
        "open_id": "ou_c53c2f904af4531eff0dba69a76c44a1"
    },
    {
        "name": "姚亮",
        "email": "liang.yao@pingcap.cn",
        "open_id": "ou_47fc28d13dd04718bbe65a3c0b010d29"
    }
]
# + DEFAULT_LEGACY_ADMINS


def push_weekly_reports(items, receivers, report_type, external=False):
    token = get_tenant_access_token(external=external)
    if not receivers:
        if report_type == "review1":
            receivers = DEFAULT_LEGACY_ADMINS if external else DEFAULT_INTERNAL_ADMINS
        elif report_type == "review1s":
            receivers = DEFAULT_EXTERNAL_ADMINS if external else DEFAULT_INTERNAL_ADMINS
        elif report_type == "review5":
            receivers = DEFAULT_MERGED_ADMINS if external else DEFAULT_MERGED_INTERNAL_ADMINS
    logger.info(receivers)
    # 组装消息
    text = ""
    if report_type == "review1" or report_type == "review1s":
        lines = [
            f"Sia帮您生成了[【{item['report_name']}】]({HOST}/review/weeklyDetail/{item['execution_id']})，请点击链接查看详情。"
            for item in items
        ]
        text = '\n'.join(lines)
    elif report_type == "review5":
        lines = [
            f"Sia帮您生成了[【{item['report_name']}】]({HOST}/review/muban5Detail/{item['execution_id']})，请点击链接查看详情。"
            for item in items
        ]
        text = '\n'.join(lines)

    
    for receiver in receivers:
        resp = send_feishu_message(receiver["open_id"], token, text)
        logger.info(f"发送给 {receiver['name']} 结果: {resp}")
