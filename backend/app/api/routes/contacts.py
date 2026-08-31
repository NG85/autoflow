import logging
from typing import Optional
from uuid import UUID
from app.api.deps import CurrentUserDep, SessionDep
from app.core.config import settings
from app.exceptions import InternalServerError
from fastapi import APIRouter, HTTPException

from app.api.routes.models import (
    LocalContactCreate,
    LocalContactUpdate,
    LocalContactResponse,
)
from app.permissions.crm_contact_permission_service import (
    CRM_ACCOUNT_VIEW_PERMISSION,
    CRM_CONTACT_CREATE_PERMISSION,
    CRM_CONTACT_DELETE_PERMISSION,
    CRM_CONTACT_EDIT_PERMISSION,
    CRM_CONTACT_VIEW_PERMISSION,
    crm_contact_permission_service,
)
from app.repositories.local_contact import local_contact_repo
from app.models.local_contacts import LocalContact


logger = logging.getLogger(__name__)

router = APIRouter()


def require_account_permission(
    db_session: SessionDep,
    user: CurrentUserDep,
    customer_id: str,
    error_message: str = "没有权限访问该客户",
    *,
    permission: str = CRM_ACCOUNT_VIEW_PERMISSION,
) -> None:
    """
    权限检查辅助函数：验证用户是否有权限访问指定的客户。

    走 OAuth ``POST /permission/check``。创建联系人传 ``permission=crm:contact:create``。

    如果权限检查失败，会抛出 HTTPException(403)
    """
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id 不能为空")

    if not local_contact_repo.check_account_permission(
        db_session,
        user.id,
        customer_id,
        permission=permission,
    ):
        raise HTTPException(status_code=403, detail=error_message)


def require_contact_permission(
    db_session: SessionDep,
    user: CurrentUserDep,
    contact_id: str,
    error_message: str = "没有权限访问该联系人所属的客户",
    *,
    permission: str = CRM_CONTACT_VIEW_PERMISSION,
) -> LocalContact:
    """
    本地联系人单条鉴权：只校验所属客户（resource=crm_account）。

    不存在 → 404；无权限 → 403。
    """
    if not contact_id:
        raise HTTPException(status_code=400, detail="contact_id 不能为空")

    contact = local_contact_repo.get_by_id(db_session, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在或无权限访问")

    if not crm_contact_permission_service.check_account_access(
        db_session,
        user.id,
        contact.customer_id,
        permission=permission,
    ):
        raise HTTPException(status_code=403, detail=error_message)

    return contact


def _contact_to_response(contact: LocalContact) -> LocalContactResponse:
    """将 LocalContact 模型转换为 LocalContactResponse"""
    # 安全获取 is_existing 属性（动态添加的，可能不存在）
    is_existing = getattr(contact, 'is_existing', None)
    
    return LocalContactResponse(
        id=contact.id,
        unique_id=contact.unique_id,
        name=contact.name,
        customer_id=contact.customer_id,
        customer_name=contact.customer_name,
        position=contact.position,
        gender=contact.gender,
        mobile=contact.mobile,
        phone=contact.phone,
        email=contact.email,
        wechat=contact.wechat,
        address=contact.address,
        key_decision_maker=contact.key_decision_maker,
        department=contact.department,
        direct_superior=contact.direct_superior,
        status=contact.status,
        source=contact.source,
        business_relationship=contact.business_relationship,
        remarks=contact.remarks,
        created_at=contact.created_at.isoformat() if contact.created_at else "",
        updated_at=contact.updated_at.isoformat() if contact.updated_at else "",
        created_by=str(contact.created_by) if contact.created_by else None,
        updated_by=str(contact.updated_by) if contact.updated_by else None,
        crm_unique_id=contact.crm_unique_id,
        synced_to_crm=contact.synced_to_crm if contact.synced_to_crm is not None else False,
        synced_at=contact.synced_at.isoformat() if contact.synced_at else None,
        is_existing=is_existing,
    )


def notify_aldebaran_local_contact_created(
    contact: LocalContact,
    *,
    user_id: Optional[UUID] = None,
) -> bool:
    """
    新建本地联系人成功后通知 Aldebaran（crm.contact.created）。
    已存在联系人、开关关闭或入队失败不影响创建结果。
    """
    if getattr(contact, "is_existing", False):
        logger.info(
            "Skip Aldebaran contact-created notify for existing contact, contact_id=%s",
            contact.unique_id,
        )
        return False
    if not settings.ALDEBARAN_CONTACT_CREATED_ENABLED:
        logger.info(
            "Aldebaran contact-created notify disabled, contact_id=%s",
            contact.unique_id,
        )
        return False
    try:
        from app.services.aldebaran_service import aldebaran_client

        aldebaran_client.trigger_local_contact_created(
            contact_id=contact.unique_id,
            customer_id=contact.customer_id,
            created_by_user_id=user_id or contact.created_by,
            event_time=contact.created_at,
        )
        return True
    except Exception as exc:
        logger.error(
            "Aldebaran contact-created notify failed, contact_id=%s: %s",
            contact.unique_id,
            exc,
            exc_info=True,
        )
        return False


@router.post("/contacts/local")
def create_local_contact(
    db_session: SessionDep,
    user: CurrentUserDep,
    contact: LocalContactCreate,
) -> dict:
    """
    创建本地联系人。新建成功后通知 Aldebaran ``crm.contact.created``（已存在则跳过）。

    权限要求：OAuth ``crm:contact:create`` + 对 ``customer_id`` 对应客户有数据权限
    （``POST /permission/check``，resource=crm_account）
    """
    try:
        contact_data = contact.model_dump(exclude_none=True)
        customer_id = contact_data.get("customer_id")
        
        # 权限检查：OAuth /permission/check → crm:contact:create + resource crm_account
        require_account_permission(
            db_session=db_session,
            user=user,
            customer_id=customer_id,
            error_message="没有权限访问该客户，无法创建联系人",
            permission=CRM_CONTACT_CREATE_PERMISSION,
        )
        
        new_contact = local_contact_repo.create(
            db_session=db_session,
            contact_data=contact_data,
            user_id=user.id
        )
        notify_aldebaran_local_contact_created(new_contact, user_id=user.id)

        # 转换为响应格式
        response = _contact_to_response(new_contact)
        
        return {
            "code": 0,
            "message": "success",
            "data": response.model_dump(),
        }
    except HTTPException:
        raise
    except ValueError as e:
        error_msg = str(e)
        # 检查是否是权限相关的错误
        if "permission" in error_msg.lower() or "权限" in error_msg:
            raise HTTPException(status_code=403, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/contacts/local")
def query_local_contacts(
    db_session: SessionDep,
    user: CurrentUserDep,
    customer_id: Optional[str] = None,
    name: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    查询本地联系人列表

    权限要求：
    - 功能门控 OAuth ``crm:contact:view``
    - 列表数据范围 OAuth ``data-scope(entity=crm_account)``（按所属客户）
    """
    try:
        if not crm_contact_permission_service.gate_view(db_session, user.id):
            raise HTTPException(status_code=403, detail="无联系人查看权限")

        # 参数验证
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20
        if page_size > 100:
            page_size = 100

        skip = (page - 1) * page_size

        contacts, total = local_contact_repo.search(
            db_session=db_session,
            user_id=user.id,
            customer_id=customer_id,
            name=name,
            skip=skip,
            limit=page_size
        )

        # 转换为响应格式
        items = []
        for contact in contacts:
            response = _contact_to_response(contact)
            items.append(response.model_dump())

        return {
            "code": 0,
            "message": "success",
            "data": {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/contacts/local/{contact_id}")
def get_local_contact(
    db_session: SessionDep,
    user: CurrentUserDep,
    contact_id: str,
) -> dict:
    """
    获取单个本地联系人详情

    权限要求：OAuth ``crm:contact:view`` + resource=crm_account（所属客户）
    """
    try:
        contact = require_contact_permission(
            db_session=db_session,
            user=user,
            contact_id=contact_id,
            permission=CRM_CONTACT_VIEW_PERMISSION,
        )

        response = _contact_to_response(contact)

        return {
            "code": 0,
            "message": "success",
            "data": response.model_dump(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/contacts/local/{contact_id}")
def update_local_contact(
    db_session: SessionDep,
    user: CurrentUserDep,
    contact_id: str,
    contact: LocalContactUpdate,
) -> dict:
    """
    更新本地联系人

    权限要求：OAuth ``crm:contact:edit`` + resource=crm_account（所属客户）
    """
    try:
        require_contact_permission(
            db_session=db_session,
            user=user,
            contact_id=contact_id,
            error_message="没有权限访问该联系人所属的客户，无法修改联系人",
            permission=CRM_CONTACT_EDIT_PERMISSION,
        )

        contact_data = contact.model_dump(exclude_none=True)

        updated_contact = local_contact_repo.update(
            db_session=db_session,
            contact_id=contact_id,
            contact_data=contact_data,
            user_id=user.id
        )

        if not updated_contact:
            raise HTTPException(status_code=404, detail="联系人不存在或无权限访问")

        response = _contact_to_response(updated_contact)

        return {
            "code": 0,
            "message": "success",
            "data": response.model_dump(),
        }
    except HTTPException:
        raise
    except ValueError as e:
        error_msg = str(e)
        if "permission" in error_msg.lower() or "权限" in error_msg:
            raise HTTPException(status_code=403, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.delete("/contacts/local/{contact_id}")
def delete_local_contact(
    db_session: SessionDep,
    user: CurrentUserDep,
    contact_id: str,
) -> dict:
    """
    删除本地联系人（软删除）

    权限要求：OAuth ``crm:contact:delete`` + resource=crm_account（所属客户）
    """
    try:
        require_contact_permission(
            db_session=db_session,
            user=user,
            contact_id=contact_id,
            error_message="没有权限访问该联系人所属的客户，无法删除联系人",
            permission=CRM_CONTACT_DELETE_PERMISSION,
        )

        success = local_contact_repo.delete(
            db_session=db_session,
            contact_id=contact_id,
            user_id=user.id
        )

        if not success:
            raise HTTPException(status_code=404, detail="联系人不存在或无权限访问")

        return {
            "code": 0,
            "message": "success",
            "data": {"id": contact_id},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/contacts/local/customer/{customer_id}")
def query_local_contacts_by_customer(
    db_session: SessionDep,
    user: CurrentUserDep,
    customer_id: str,
    page: int = 1,
    page_size: int = 100,
) -> dict:
    """
    根据客户ID获取该客户下的所有本地联系人

    权限要求（与独立列表对齐）：
    - 功能门控 OAuth ``crm:contact:view``
    - 单客户数据权限 ``crm:contact:view`` + resource=crm_account
    """
    try:
        if not crm_contact_permission_service.gate_view(db_session, user.id):
            raise HTTPException(status_code=403, detail="无联系人查看权限")

        require_account_permission(
            db_session=db_session,
            user=user,
            customer_id=customer_id,
            error_message="没有权限访问该客户，无法获取联系人列表",
            permission=CRM_CONTACT_VIEW_PERMISSION,
        )

        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 100
        if page_size > 100:
            page_size = 100

        skip = (page - 1) * page_size

        contacts, total = local_contact_repo.get_by_customer_id(
            db_session=db_session,
            customer_id=customer_id,
            user_id=user.id,
            skip=skip,
            limit=page_size
        )

        items = []
        for contact in contacts:
            response = _contact_to_response(contact)
            items.append(response.model_dump())

        return {
            "code": 0,
            "message": "success",
            "data": {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
