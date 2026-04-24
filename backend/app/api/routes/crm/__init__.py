"""
CRM HTTP 路由包。

- 对外入口：``app.api.routes.crm.routes`` → ``router``（由 ``app.api.main`` 挂载，URL 不变）。
- 子域实现：``review/``、``weekly_followup/``、``visit_records/``、``documents/``、``views/``。
- 聚合顺序与实现：``compose.build_crm_router()``；``routes.router`` 即其返回值，避免与 ``compose`` 双份维护。
"""
