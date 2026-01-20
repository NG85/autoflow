import logging
from typing import Optional
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from fastapi import APIRouter, HTTPException

from app.api.routes.models import (
    LocalContactCreate,
    LocalContactUpdate,
    LocalContactResponse,
)
from app.repositories.local_contact import local_contact_repo
from app.models.local_contacts import LocalContact


logger = logging.getLogger(__name__)

router = APIRouter()


def require_account_permission(
    db_session: SessionDep,
    user: CurrentUserDep,
    customer_id: str,
    error_message: str = "没有权限访问该客户"
) -> None:
    """
    权限检查辅助函数：验证用户是否有权限访问指定的客户
    
    如果权限检查失败，会抛出 HTTPException(403)
    
    Args:
        db_session: 数据库会话
        user: 当前用户
        customer_id: 客户ID
        error_message: 权限不足时的错误消息
    """
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id 不能为空")
    
    if not local_contact_repo.check_account_permission(db_session, user.id, customer_id):
        raise HTTPException(status_code=403, detail=error_message)


def require_contact_permission(
    db_session: SessionDep,
    user: CurrentUserDep,
    contact_id: str,
    error_message: str = "没有权限访问该联系人所属的客户"
) -> LocalContact:
    """
    权限检查辅助函数：验证用户是否有权限访问指定联系人所属的客户
    
    如果权限检查失败，会抛出 HTTPException(403) 或 HTTPException(404)
    如果权限检查通过，返回联系人对象
    
    Args:
        db_session: 数据库会话
        user: 当前用户
        contact_id: 联系人ID
        error_message: 权限不足时的错误消息
    
    Returns:
        联系人对象
    """
    if not contact_id:
        raise HTTPException(status_code=400, detail="contact_id 不能为空")
    
    contact = local_contact_repo.get_by_id(db_session, contact_id, user.id)
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在或无权限访问")
    
    if not local_contact_repo.check_account_permission(db_session, user.id, contact.customer_id):
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


@router.post("/contacts/local")
def create_local_contact(
    db_session: SessionDep,
    user: CurrentUserDep,
    contact: LocalContactCreate,
) -> dict:
    """
    创建本地联系人
    
    权限要求：用户必须有权限访问指定的客户
    """
    try:
        contact_data = contact.model_dump(exclude_none=True)
        customer_id = contact_data.get("customer_id")
        
        # 权限检查
        require_account_permission(
            db_session=db_session,
            user=user,
            customer_id=customer_id,
            error_message=f"没有权限访问客户 {customer_id}，无法创建联系人"
        )
        
        new_contact = local_contact_repo.create(
            db_session=db_session,
            contact_data=contact_data,
            user_id=user.id
        )
        
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
    
    权限要求：只返回用户有权限访问的客户下的联系人
    
    Args:
        customer_id: 可选的客户ID过滤
        name: 可选的姓名搜索（模糊匹配）
        page: 页码，默认1
        page_size: 每页数量，默认20，最大100
    """
    try:
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
    
    权限要求：用户必须有权限访问该联系人所属的客户
    """
    try:
        # 权限检查（会返回联系人对象）
        contact = require_contact_permission(
            db_session=db_session,
            user=user,
            contact_id=contact_id
        )
        
        # 转换为响应格式
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
    
    权限要求：用户必须有权限访问该联系人所属的客户
    """
    try:
        # 权限检查（会返回联系人对象）
        require_contact_permission(
            db_session=db_session,
            user=user,
            contact_id=contact_id,
            error_message=f"没有权限访问该联系人所属的客户，无法修改联系人"
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
        
        # 转换为响应格式
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
        # 检查是否是权限相关的错误
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
    
    权限要求：用户必须有权限访问该联系人所属的客户
    """
    try:
        # 权限检查（会返回联系人对象）
        require_contact_permission(
            db_session=db_session,
            user=user,
            contact_id=contact_id,
            error_message=f"没有权限访问该联系人所属的客户，无法删除联系人"
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
    
    权限要求：用户必须有权限访问指定的客户
    
    Args:
        customer_id: 客户ID
        page: 页码，默认1
        page_size: 每页数量，默认100，最大100
    """
    try:
        # 权限检查
        require_account_permission(
            db_session=db_session,
            user=user,
            customer_id=customer_id,
            error_message=f"没有权限访问客户 {customer_id}"
        )
        
        # 参数验证
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
