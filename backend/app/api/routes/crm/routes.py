import logging
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from fastapi import APIRouter, Body, HTTPException
from app.crm.view_engine import CrmViewRequest, ViewType, CrmViewEngine, ViewRegistry
from fastapi_pagination import Page

from app.api.routes.crm.models import Account, VisitRecordCreate
from app.crm.save_engine import (
    save_visit_record_to_crm_table, 
    check_followup_quality, 
    check_next_steps_quality, 
    push_visit_record_feishu_message
)


logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize view registry
view_registry = ViewRegistry()

# Initialize view engine
view_engine = CrmViewEngine(view_registry=view_registry)


@router.post("/crm/views", response_model=Page[Account])
def query_crm_view(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: CrmViewRequest,
):
    try:
        # 使用 execute_view_query 获取分页数据
        result = view_engine.execute_view_query(
            db_session=db_session,
            request=request,
            user_id=user.id
        )
        
        # 转换为 Page 格式
        return Page(
            items=result["data"],
            total=result["total"],
            page=result["page"],
            size=result["page_size"],
            pages=result["total_pages"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.get("/crm/views/fields")
async def get_view_fields(
    view_type: ViewType = ViewType.STANDARD
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
            user_id=user.id
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_record")
def create_visit_record(
    user: CurrentUserDep,
    record: VisitRecordCreate,
    external: bool = Body(False, example=False),
    force: bool = Body(False, example=False)
):
    try:
        if force:
            # 直接保存，不做AI判断
            save_visit_record_to_crm_table(record)
            push_visit_record_feishu_message(
                external=external,
                sales_visit_record={
                    **record.model_dump()
                }
            )
            return {"code": 0, "message": "success", "data": {}}
        
        # 评估跟进质量
        followup_quality_level, followup_quality_reason = check_followup_quality(record.followup_record)
        next_steps_quality_level, next_steps_quality_reason = check_next_steps_quality(record.next_steps)
        data = {
            "followup": {"level": followup_quality_level, "reason": followup_quality_reason},
            "next_steps": {"level": next_steps_quality_level, "reason": next_steps_quality_reason}
        }
        # 只要有一项不合格就阻止保存
        if followup_quality_level == "不合格" or next_steps_quality_level == "不合格":
            return {"code": 400, "message": "failed", "data": data}

        record.followup_quality_level = followup_quality_level
        record.followup_quality_reason = followup_quality_reason
        record.next_steps_quality_level = next_steps_quality_level
        record.next_steps_quality_reason = next_steps_quality_reason
        save_visit_record_to_crm_table(record)
        # 推送飞书消息
        push_visit_record_feishu_message(
            external=external,
            sales_visit_record={
                **record.model_dump()
            }
        )
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
    


@router.post("/crm/visit_record/verify")
def verify_visit_record(
    user: CurrentUserDep,
    followup_record: str = Body(..., example=""),
    next_steps: str = Body(..., example=""),
):
    try:
        followup_quality_level, followup_quality_reason = check_followup_quality(followup_record)
        next_steps_quality_level, next_steps_quality_reason = check_next_steps_quality(next_steps)
        data = {
            "followup": {"level": followup_quality_level, "reason": followup_quality_reason},
            "next_steps": {"level": next_steps_quality_level, "reason": next_steps_quality_reason}
        }        
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()