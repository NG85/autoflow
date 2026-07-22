import logging
from uuid import UUID
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel
from app.core.config import settings
from app.rag.types import CrmDataType
from sqlmodel import Session

from app.core.db import engine
from app.repositories.crm_data_authority import crm_data_authority_repo
from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

# RAG 知识过滤主要依赖客户/商机；data-scope 取这两类后按 crm_ids 物化 mirror。
_RAG_SCOPE_ENTITIES: tuple[str, ...] = (
    CrmDataType.ACCOUNT.value,
    CrmDataType.OPPORTUNITY.value,
)

    
class CRMAuthorityItem(BaseModel):
    """CRM authority item"""
    dataId: str
    type: str
    userId: str
  
class CRMAuthorityResponse(BaseModel):
    """CRM authority API response"""
    code: int
    message: str
    result: List[CRMAuthorityItem]
      
class CRMAuthority(BaseModel):
    """CRM authority data structure"""
    authorized_items: Dict[str, Set[str]] = {}  # data type -> data ID set
    truncated: bool = False  # whether the authorized_items was truncated due to size limit
    
    def is_authorized(self, data_type: str, data_id: str) -> bool:
        """
        Check if the specified type of data ID has access permission
        
        Args:
            data_type: data type, e.g. 'crm_account', 'crm_opportunity'
            data_id: data ID
            
        Returns:
            Whether there is access permission
        """
        if data_type == CrmDataType.INTERNAL_OWNER:
            return True
        if data_type not in self.authorized_items:
            return False
        return data_id in self.authorized_items[data_type]
    
    def is_authorized_opportunity(self, opportunity_id: str) -> bool:
        """Check if the opportunity ID has access permission"""
        return self.is_authorized(CrmDataType.OPPORTUNITY, opportunity_id)
        
    def is_authorized_account(self, account_id: str) -> bool:
        """Check if the customer ID has access permission"""
        return self.is_authorized(CrmDataType.ACCOUNT, account_id)
    
    def is_authorized_contact(self, contact_id: str) -> bool:
        """Check if the contact ID has access permission"""
        return self.is_authorized(CrmDataType.CONTACT, contact_id)
    
    def is_authorized_order(self, order_id: str) -> bool:
        """Check if the order ID has access permission"""
        return self.is_authorized(CrmDataType.ORDER, order_id)
    
    def is_authorized_payment_plan(self, payment_plan_id: str) -> bool:
        """Check if the payment plan ID has access permission"""
        return self.is_authorized(CrmDataType.PAYMENTPLAN, payment_plan_id)
    
    def is_authorized_stage(self, stage_id: str) -> bool:
        """Check if the stage ID has access permission"""
        return self.is_authorized(CrmDataType.STAGE, stage_id)
    
    def is_authorized_sales_record(self, sales_record_id: str) -> bool:
        """Check if the sales record ID has access permission"""
        return self.is_authorized(CrmDataType.SALES_RECORD, sales_record_id)
        
    def is_empty(self) -> bool:
        """Check if there is any authorized data"""
        return len(self.authorized_items) == 0 or all(len(ids) == 0 for ids in self.authorized_items.values())


def _scope_filters(scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(scope, dict):
        return []
    filters = scope.get("filters")
    return filters if isinstance(filters, list) else []


def _source(item: dict[str, Any]) -> str:
    value = item.get("source")
    return str(value).strip() if value is not None else ""


def _get_bool(item: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = item.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.lower() == "true":
            return True
    return False


def _get_str(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _get_str_list(item: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
    return []


def scope_has_global(filters: list[dict[str, Any]] | None) -> bool:
    """OAuth data-scope ``global.enabled`` → RAG 不过滤 CRM（等同旧 admin）。"""
    for item in filters or []:
        if _source(item) == "global" and _get_bool(item, "enabled"):
            return True
    return False


def extract_mirror_crm_ids(filters: list[dict[str, Any]] | None) -> list[str]:
    """从 data-scope filters 提取 mirror 用的 crm_id 集合（本人 + org_scope）。"""
    ids: list[str] = []
    seen: set[str] = set()
    for item in filters or []:
        src = _source(item)
        if src == "crm_data_authority":
            crm_id = _get_str(item, "crmId", "crm_id")
            if crm_id and crm_id not in seen:
                seen.add(crm_id)
                ids.append(crm_id)
        elif src == "org_scope" and _get_bool(item, "mirrorMatch", "mirror_match"):
            for org_id in _get_str_list(item, "crmUserIds", "crm_user_ids"):
                if org_id not in seen:
                    seen.add(org_id)
                    ids.append(org_id)
    return ids


def _resolve_scope_entities(crm_type: Optional[CrmDataType]) -> list[str]:
    if crm_type is None:
        return list(_RAG_SCOPE_ENTITIES)
    if crm_type.value in _RAG_SCOPE_ENTITIES:
        return [crm_type.value]
    # 其他 CRM 类型：仍用 account+opportunity 的 org/self crm_ids 去 mirror 物化该 type
    return list(_RAG_SCOPE_ENTITIES)


def get_user_crm_authority(user_id: UUID, crm_type: Optional[CrmDataType] = None) -> Tuple[CRMAuthority, str]:
    """Get CRM data access permission for RAG filtering.

    真源：OAuth ``data-scope``（crm_account / crm_opportunity）→ 按 filters 中的
    crm_ids 从 ``crm_data_authority`` 物化 ID 集合，供 metadata IN / 后滤使用。
    ``global`` → 返回空 authority + role=admin（下游不施加 CRM 过滤）。
    """
    authority = CRMAuthority()
    role = None
    try:
        with Session(engine) as session:
            crm_user_id = user_profile_repo.get_crm_user_id_by_user_id(session, user_id)

            scope_entities = _resolve_scope_entities(crm_type)
            scopes: list[dict[str, Any]] = []
            for entity in scope_entities:
                scopes.append(
                    oauth_client.get_data_scope(
                        user_id=user_id,
                        crm_user_id=crm_user_id,
                        entity=entity,
                    )
                )

            if scopes and all(scope_has_global(_scope_filters(s)) for s in scopes):
                logger.info(
                    "User %s has global CRM data-scope for %s; skip materializing authority.",
                    user_id,
                    scope_entities,
                )
                return authority, "admin"

            crm_ids: list[str] = []
            seen_ids: set[str] = set()
            for scope in scopes:
                for cid in extract_mirror_crm_ids(_scope_filters(scope)):
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        crm_ids.append(cid)

            if not crm_ids:
                logger.info(
                    "User %s has no mirror crm_ids from data-scope entities=%s; empty authority.",
                    user_id,
                    scope_entities,
                )
                return authority, None

            max_rows = max(int(getattr(settings, "CRM_AUTHORITY_MAX_ROWS", 50000)), 1)
            authority_types: Optional[list[str]] = [crm_type.value] if crm_type else None
            rows = crm_data_authority_repo.list_authority_rows(
                session,
                crm_ids=crm_ids,
                authority_types=authority_types,
                max_rows=max_rows,
            )
            if len(rows) > max_rows:
                authority.truncated = True
                rows = rows[:max_rows]
                logger.warning(
                    "CRM authority rows exceeded limit (max_rows=%s) for user=%s "
                    "crm_ids=%s crm_type=%s. Authority set truncated; safe but may reduce recall.",
                    max_rows,
                    user_id,
                    len(crm_ids),
                    crm_type.value if crm_type else None,
                )

            for data_type, data_id in rows:
                try:
                    mapped_type = CrmDataType(data_type)
                except ValueError:
                    logger.warning(
                        "Unknown CRM data type from table crm_data_authority: %s",
                        data_type,
                    )
                    continue
                authority.authorized_items.setdefault(mapped_type, set()).add(data_id)

        stats = {data_type: len(ids) for data_type, ids in authority.authorized_items.items()}
        logger.info(
            "User %s CRM authority materialized from OAuth data-scope: crm_ids=%s stats=%s truncated=%s",
            user_id,
            len(crm_ids),
            stats,
            authority.truncated,
        )
        return authority, role

    except Exception as e:
        logger.error(f"Failed to get CRM authority for user {user_id}: {e}", exc_info=True)
        return authority, None


def identify_crm_data_type(data_object, meta_or_metadata: str = "meta") -> tuple[Optional[str], Optional[str]]:
    """
    Identify the CRM type and ID of the entity/relationship, only process the entity/relationship with the CRM type mark
    
    Args:
        data_object: Knowledge graph entity/relationship
        
    Returns:
        Tuple (entity type, entity ID) if not a CRM entity or cannot be identified, return (None, None)
    """
    # Get metadata
    meta = getattr(data_object, meta_or_metadata, {}) or {}
    
    # First check if there is a crm_data_type field and it is a CRM type
    data_type = meta.get("crm_data_type")
    crm_type = get_crm_type(data_type)
    
    # If not a CRM type, return None
    if not crm_type:
        return None, None
    
    # Get ID fields based on CRM type
    id_fields_map = {
        CrmDataType.ACCOUNT: ["account_id", "customer_id", "unique_id"],
        CrmDataType.CONTACT: ["contact_id", "unique_id"],
        CrmDataType.INTERNAL_OWNER: ["internal_owner", "unique_id"],
        CrmDataType.OPPORTUNITY: ["opportunity_id", "unique_id"],
        CrmDataType.ORDER: ["sales_order_number", "unique_id"],
        CrmDataType.PAYMENTPLAN: ["name", "unique_id"],
        CrmDataType.STAGE: ["stage_id", "unique_id"],
        CrmDataType.SALES_RECORD: ["sales_record_id", "unique_id"]
        # TODO: Add more other CRM types
    }
    
    # Find ID
    if crm_type in id_fields_map:
        for id_field in id_fields_map[crm_type]:
            if id_field in meta and meta[id_field]:
                return crm_type, meta[id_field]
    
    # No valid ID found
    return crm_type, None

def is_crm_data_type(crm_data_type: Any) -> bool:
    """Check if the given crm_data_type is a valid CRM type"""
    if not crm_data_type:
        return False
        
    try:
        # Check if it is one of the enum values
        return any(crm_data_type == data_type.value for data_type in CrmDataType)
    except (ValueError, TypeError):
        return False

def get_crm_type(crm_data_type: Any) -> Optional[CrmDataType]:
    """Get the CRM type enum corresponding to the crm_data_type"""
    if not is_crm_data_type(crm_data_type):
        return None
        
    try:
        return CrmDataType(crm_data_type)
    except (ValueError, TypeError):
        return None
