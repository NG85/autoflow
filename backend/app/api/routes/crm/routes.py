"""CRM HTTP 路由聚合：委托 ``compose.build_crm_router``，与 ``main`` 挂载入口兼容。"""

from app.api.routes.crm.compose import build_crm_router

router = build_crm_router()
