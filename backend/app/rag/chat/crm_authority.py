import logging
from uuid import UUID
import requests
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel
from app.core.config import settings
from app.rag.types import CrmDataType

logger = logging.getLogger(__name__)

    
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
    
    def is_authorized(self, data_type: str, data_id: str) -> bool:
        """
        Check if the specified type of data ID has access permission
        
        Args:
            data_type: data type, e.g. 'crm_account', 'crm_opportunity'
            data_id: data ID
            
        Returns:
            Whether there is access permission
        """
        if data_type == CrmDataType.INTERNAL_OWNER or data_type == CrmDataType.OPPORTUNITY_UPDATES:
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
    
    def is_authorized_opportunity_updates(self, opportunity_id: str) -> bool:
        """Check if the opportunity updates ID has access permission"""
        return self.is_authorized(CrmDataType.OPPORTUNITY_UPDATES, opportunity_id)
    
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


def get_user_crm_authority(user_id: Optional[UUID]) -> Tuple[CRMAuthority, Optional[str]]:
    """Get the CRM data access permission of the user"""
    if not user_id:
        logger.info("Anonymous user has no CRM data access")
        return CRMAuthority(), None
    
    authority = CRMAuthority()
            
    # Get API URL from configuration
    auth_api_url = settings.CRM_AUTHORITY_API_URL
    if not auth_api_url:
        logger.error("CRM_AUTHORITY_API_URL is not configured")
        return authority, None
    
    try:
        # Build request body
        payload = {
            "dataId": "",
            "highSeasAccounts": False,
            "type": "crm_opportunity",
            "userId": str(user_id)
        }
        
        # Send POST request
        response = requests.post(auth_api_url, json=payload, timeout=300)
        
        # Check HTTP response status
        if response.status_code != 200:
            logger.error(f"CRM authority API HTTP error: {response.status_code}")
            return authority, None
            
        # Parse response JSON
        try:
            data = response.json()
        except ValueError:
            logger.error("CRM authority API returned invalid JSON")
            return authority, None
            
        # Verify response format
        if not isinstance(data, dict) or "code" not in data or "result" not in data:
            logger.error(f"CRM authority API returned unexpected format: {data}")
            return authority, None
            
        # Check response status code
        if data["code"] != 0:
            logger.error(f"CRM authority API error: {data.get('message', 'Unknown error')}")
            return authority, None
            
        # Process authority data
        result = data.get("result", {})
        if not isinstance(result, dict):
            logger.error(f"CRM authority API returned invalid result format: {result}")
            return authority, None
               
        # Handle role
        role = result.get("role", None)
        if role and role == "admin":
            return authority, role
        
        # Handle authList
        auth_list = result.get("authList", [])
        if not isinstance(auth_list, list):
            logger.error(f"CRM authority API returned invalid authList format: {auth_list}")
            return authority, role
                 
        for item in auth_list:
            if not isinstance(item, dict) or "dataId" not in item or "type" not in item:
                continue
                
            data_type = item["type"]
            data_id = item["dataId"]
            
            if not data_type or not data_id:
                continue
                
            # Map the API returned type to the CrmDataType enum
            try:
                crm_type = CrmDataType(data_type)
                authority.authorized_items.setdefault(crm_type, set()).add(data_id)
            except ValueError:
                logger.warning(f"Unknown CRM data type from API: {data_type}")
          
        # Handle highSeasAccounts
        high_seas_accounts = result.get("highSeasAccounts", [])
        if isinstance(high_seas_accounts, list):
            authority.authorized_items.setdefault(CrmDataType.ACCOUNT, set()).update(high_seas_accounts)
             
        # Record authority statistics
        stats = {data_type: len(ids) for data_type, ids in authority.authorized_items.items()}
        logger.info(f"User {user_id} CRM authority fetched: {stats}")
        
        return authority, role
        
    except Exception as e:
        logger.error(f"Failed to get CRM authority for user {user_id}: {e}", exc_info=True)
        # Return empty authority when error, ensuring security
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
        CrmDataType.OPPORTUNITY_UPDATES: ["opportunity_id", "updates_group_id", "unique_id"],
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