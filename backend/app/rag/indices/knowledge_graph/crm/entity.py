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

class ReviewSessionEntity(CRMEntityBase):
    """Entity representing a CRM review session (department-level periodic review)"""
    CRM_DATA_TYPE = CrmDataType.REVIEW_SESSION

class ReviewSnapshotEntity(CRMEntityBase):
    """Entity representing an opportunity branch snapshot within a review"""
    CRM_DATA_TYPE = CrmDataType.REVIEW_SNAPSHOT

class ReviewRiskProgressEntity(CRMEntityBase):
    """Entity representing a risk or progress insight detected during review"""
    CRM_DATA_TYPE = CrmDataType.REVIEW_RISK_PROGRESS


class ReviewWeekEntity(CRMEntityBase):
    """Entity representing a review week timeline node."""
    CRM_DATA_TYPE = "crm_review_week"


class ReviewDepartmentEntity(CRMEntityBase):
    """Entity representing a review department timeline scope node."""
    CRM_DATA_TYPE = "crm_review_department"


class ReviewRecommendationEntity(CRMEntityBase):
    """Entity representing a review recommendation feedback item."""
    CRM_DATA_TYPE = "crm_review_recommendation"
