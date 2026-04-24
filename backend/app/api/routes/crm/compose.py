"""
CRM 路由聚合：唯一挂载顺序定义处。

``app.api.routes.crm.routes`` 与 ``build_crm_router()`` 使用同一顺序，避免漂移。

各子 ``router`` 装饰器须为完整 ``/crm/...`` 路径，勿对子 ``APIRouter`` 使用会改变 URL 的 ``prefix``。
"""

from fastapi import APIRouter


def build_crm_router() -> APIRouter:
    from app.api.routes.crm.documents.router import router as documents_router
    from app.api.routes.crm.review.router import router as review_router
    from app.api.routes.crm.visit_records.router import router as visit_records_router
    from app.api.routes.crm.views.router import router as views_router
    from app.api.routes.crm.weekly_followup.router import router as weekly_followup_router

    root = APIRouter()
    root.include_router(review_router)
    root.include_router(weekly_followup_router)
    root.include_router(visit_records_router)
    root.include_router(documents_router)
    root.include_router(views_router)
    return root
