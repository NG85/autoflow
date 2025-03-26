from typing import ClassVar
from pydantic import model_validator
from app.rag.indices.knowledge_graph.schema import Entity
from app.rag.chat.crm_authority import CrmDataType

class CRMEntityBase(Entity):
    """Base class for CRM entities"""
    
    CATEGORY: ClassVar = None  # Will be overridden by subclasses
    
    @model_validator(mode='after')
    def ensure_category(self):
        if isinstance(self.metadata, dict) and self.CATEGORY:
            self.metadata = dict(self.metadata)
            self.metadata["category"] = self.CATEGORY
        return self

class AccountEntity(CRMEntityBase):
    """Entity representing a CRM account"""
    CATEGORY = CrmDataType.ACCOUNT
    
class ContactEntity(CRMEntityBase):
    """Entity representing a CRM contact"""
    CATEGORY = CrmDataType.CONTACT

class OpportunityEntity(CRMEntityBase):
    """Entity representing a CRM opportunity"""
    CATEGORY = CrmDataType.OPPORTUNITY
