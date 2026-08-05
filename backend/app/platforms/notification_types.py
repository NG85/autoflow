"""
推送权限类型常量定义
"""

# 推送类型常量
NOTIFICATION_TYPE_REPORT_1 = "review1"        # 模板1推送
NOTIFICATION_TYPE_REPORT_5 = "review5"        # 模板5推送
NOTIFICATION_TYPE_WEEKLY_REPORT = "weekly_report"        # 周报推送
NOTIFICATION_TYPE_DAILY_REPORT = "daily_report"          # 日报推送
NOTIFICATION_TYPE_VISIT_RECORD = "visit_record"          # 拜访记录推送
NOTIFICATION_TYPE_LIST_VISIT_RECORDS = "list_visit_records"          # 查看拜访记录列表
NOTIFICATION_TYPE_SALES_TASK = "sales_task"              # 销售任务卡片推送

# 卡片接收资格（OAuth function permission）
# - 日报/周报：路由按业务名单后再 gate；群推送不校验
# - 跟进/拜访卡片：仅命名对齐；抄送走 notification_cc_rules，**不开启** receive gate
PERM_DAILY_REPORT_PERSONAL_RECEIVE = "notification:daily_report_personal:receive"
PERM_DAILY_REPORT_TEAM_RECEIVE = "notification:daily_report_team:receive"
PERM_DAILY_REPORT_COMPANY_RECEIVE = "notification:daily_report_company:receive"
PERM_WEEKLY_REPORT_TEAM_RECEIVE = "notification:weekly_report_team:receive"
PERM_WEEKLY_REPORT_COMPANY_RECEIVE = "notification:weekly_report_company:receive"
PERM_FOLLOW_UP_CARD_RECEIVE = "notification:follow_up_card:receive"
# Legacy alias（OAuth maps_to）；业务与文档一律用 PERM_FOLLOW_UP_CARD_RECEIVE，勿再硬编码
LEGACY_VISIT_RECORD_CARD_RECEIVE = "visit_record:card:receive"

# 所有推送类型列表
ALL_NOTIFICATION_TYPES = [
    NOTIFICATION_TYPE_REPORT_1,
    NOTIFICATION_TYPE_REPORT_5,
    NOTIFICATION_TYPE_WEEKLY_REPORT,
    NOTIFICATION_TYPE_DAILY_REPORT,
    NOTIFICATION_TYPE_VISIT_RECORD,
    NOTIFICATION_TYPE_LIST_VISIT_RECORDS,
    NOTIFICATION_TYPE_SALES_TASK,
]

# 推送类型描述
NOTIFICATION_TYPE_DESCRIPTIONS = {
    NOTIFICATION_TYPE_REPORT_1: "模板1推送",
    NOTIFICATION_TYPE_REPORT_5: "模板5推送",
    NOTIFICATION_TYPE_WEEKLY_REPORT: "周报推送",
    NOTIFICATION_TYPE_DAILY_REPORT: "日报推送",
    NOTIFICATION_TYPE_VISIT_RECORD: "拜访记录推送",
    NOTIFICATION_TYPE_LIST_VISIT_RECORDS: "查看拜访记录列表",
    NOTIFICATION_TYPE_SALES_TASK: "销售任务卡片推送",
}

# 默认权限配置 - 可以根据角色设置默认权限
DEFAULT_PERMISSIONS = {
    "admin": ALL_NOTIFICATION_TYPES,  # 管理员拥有所有权限
    "extend_admin": [
        NOTIFICATION_TYPE_REPORT_1,
        NOTIFICATION_TYPE_REPORT_5,
        NOTIFICATION_TYPE_WEEKLY_REPORT,
        NOTIFICATION_TYPE_LIST_VISIT_RECORDS,
    ],
    "manager": [
        NOTIFICATION_TYPE_DAILY_REPORT,
        NOTIFICATION_TYPE_LIST_VISIT_RECORDS,
    ],
    "extend_manager": [
        NOTIFICATION_TYPE_DAILY_REPORT,
        NOTIFICATION_TYPE_LIST_VISIT_RECORDS,
        NOTIFICATION_TYPE_VISIT_RECORD,
    ],
    "leader": [
        NOTIFICATION_TYPE_WEEKLY_REPORT,
        NOTIFICATION_TYPE_DAILY_REPORT,
        NOTIFICATION_TYPE_VISIT_RECORD,
    ],
    "sales": [
        NOTIFICATION_TYPE_DAILY_REPORT,
        NOTIFICATION_TYPE_VISIT_RECORD,
        NOTIFICATION_TYPE_SALES_TASK,
    ],
}
