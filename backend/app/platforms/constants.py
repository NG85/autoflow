"""
平台相关常量定义
"""

# 支持的文档类型
SUPPORTED_DOCUMENT_TYPES = [
    'doc',      # 飞书旧版文档
    'docx',     # 飞书新版文档
    'minutes',  # 会议纪要
    'wiki_node' # 知识库
]

# 平台名称常量
PLATFORM_FEISHU = "feishu"
PLATFORM_LARK = "lark"

# 支持的平台列表
SUPPORTED_PLATFORMS = [
    PLATFORM_FEISHU,
    PLATFORM_LARK,
]

# 内部应用ID列表
INTERNAL_APP_IDS = [
    'cli_a74a312d91b9d00d', # feishu
    'cli_a8294dbbcdb8d02d', # lark
    'cli_a808bc341680d00b', # feishu test
]

DEFAULT_INTERNAL_GROUP_CHATS = [
    {
        "client_id": "cli_a74a312d91b9d00d", # feishu
        "name": "集结号",
        "chat_id": "oc_9b167146c8e0d78121898641fd91d61b",
        "platform": PLATFORM_FEISHU
    },
    {
        "client_id": "cli_a8294dbbcdb8d02d", # lark
        "name": "Notification",
        "chat_id": "oc_2ffa5b05aee80dde0ddc78e6971982dd",
        "platform": PLATFORM_LARK
    },
    {
        "client_id": "cli_a808bc341680d00b", # feishu test
        "name": "拜访跟进",
        "chat_id": "oc_695eb4b33d0835d58181be4d47ab7494",
        "platform": PLATFORM_FEISHU
    },
]