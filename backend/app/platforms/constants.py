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
PLATFORM_DINGTALK = "dingtalk"

# 支持的平台列表
SUPPORTED_PLATFORMS = [
    PLATFORM_FEISHU,
    PLATFORM_LARK,
    PLATFORM_DINGTALK,
]
