from typing import ClassVar
from pydantic import model_validator
from app.rag.indices.knowledge_graph.schema import Entity
from app.rag.chat.crm_authority import CrmDataType

class CRMEntityBase(Entity):
    """Base class for CRM entities"""
    
    CRM_DATA_TYPE: ClassVar = None  # Will be overridden by subclasses
    
    @model_validator(mode='after')
    def ensure_category(self):
        if isinstance(self.metadata, dict) and self.CRM_DATA_TYPE:
            self.metadata = dict(self.metadata)
            self.metadata["crm_data_type"] = self.CRM_DATA_TYPE
        return self

class AccountEntity(CRMEntityBase):
    """Entity representing a CRM account"""
    CRM_DATA_TYPE = CrmDataType.ACCOUNT
    
class ContactEntity(CRMEntityBase):
    """Entity representing a CRM contact"""
    CRM_DATA_TYPE = CrmDataType.CONTACT

class InternalOwnerEntity(CRMEntityBase):
    """Entity representing a CRM internal owner"""
    CRM_DATA_TYPE = CrmDataType.INTERNAL_OWNER
class OpportunityEntity(CRMEntityBase):
    """Entity representing a CRM opportunity"""
    CRM_DATA_TYPE = CrmDataType.OPPORTUNITY

class OrderEntity(CRMEntityBase):
    """Entity representing a CRM order"""
    CRM_DATA_TYPE = CrmDataType.ORDER

class PaymentPlanEntity(CRMEntityBase):
    """Entity representing a CRM payment plan"""
    CRM_DATA_TYPE = CrmDataType.PAYMENTPLAN
    