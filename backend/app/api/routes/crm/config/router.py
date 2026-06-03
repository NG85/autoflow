"""CRM 系统配置 HTTP 路由。"""

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUserDep, SessionDep
from app.api.routes.crm.models import VisitRecordFieldMappingOut
from app.exceptions import InternalServerError
from app.services.crm_config_service import get_resolved_field_mapping

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/config"])


@router.get("/crm/field-mapping")
def get_visit_record_field_mapping(
    db_session: SessionDep,
    user: CurrentUserDep,
) -> VisitRecordFieldMappingOut:
    """
    查询拜访记录/卡片/通知等使用的字段标题映射。
    返回默认值与 crm_system_configurations（VisitRecordFieldMapping）合并后的生效配置。
    """
    try:
        mapping = get_resolved_field_mapping(db_session, report_type="字段映射查询")
        return VisitRecordFieldMappingOut(mapping=mapping)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
