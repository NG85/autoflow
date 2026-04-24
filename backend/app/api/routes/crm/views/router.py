"""CRM 列表视图（CrmViewEngine / ViewRegistry）HTTP 路由。"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi_pagination import Page

from app.api.deps import CurrentUserDep, SessionDep
from app.api.routes.crm.models import Account
from app.api.routes.crm.views.engine import view_engine
from app.crm.view_engine import CrmViewRequest, ViewType
from app.exceptions import InternalServerError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/views"])


@router.post("/crm/views", response_model=Page[Account])
def query_crm_view(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: CrmViewRequest,
):
    try:
        result = view_engine.execute_view_query(
            db_session=db_session,
            request=request,
            user_id=user.id,
        )

        return Page(
            items=result["data"],
            total=result["total"],
            page=result["page"],
            size=result["page_size"],
            pages=result["total_pages"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/views/fields")
async def get_view_fields(
    view_type: ViewType = ViewType.STANDARD,
):
    try:
        fields = view_engine.view_registry.get_all_fields()
        return fields
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/views/filter-options")
async def get_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
):
    try:
        return view_engine.get_filter_options(
            db_session=db_session,
            user_id=user.id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
