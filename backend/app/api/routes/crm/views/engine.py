"""CRM 列表视图引擎单例：``ViewRegistry`` + ``CrmViewEngine``。"""

from app.crm.view_engine import CrmViewEngine, ViewRegistry

view_registry = ViewRegistry()
view_engine = CrmViewEngine(view_registry=view_registry)

__all__ = ["view_engine", "view_registry"]
