from datetime import date, datetime
from typing import Dict, Generator, Optional
from sqlmodel import Session, select, func
from app.models import Document
from app.rag.datasource.base import BaseDataSource
from pydantic import BaseModel
import logging
from app.models.crm_accounts import CRMAccount
from app.models.crm_contacts import CRMContact
from app.models.crm_opportunities import CRMOpportunity
from sqlalchemy import text
from app.core.db import get_db_session
from app.types import MimeTypes
from app.rag.datasource.crm_format import(
    format_account_info,
    format_contact_info,
    format_opportunity_info,
    get_column_comments_and_names
)
from app.models.document import DocumentCategory
from app.rag.types import CrmDataType

logger = logging.getLogger(__name__)


class CRMDataSourceConfig(BaseModel):
    """Config for CRM data source"""
    include_accounts: bool = True
    include_contacts: bool = True
    include_updates: bool = True
    include_opportunities: bool = True
    account_filter: Optional[str] = None
    contact_filter: Optional[str] = None
    opportunity_filter: Optional[str] = None
    max_count: Optional[int] = None
    batch_size: int = 100

class CRMDataSource(BaseDataSource):
    def validate_config(self):
        if isinstance(self.config, list):
            self.config_obj = CRMDataSourceConfig.model_validate(self.config[0])
        elif isinstance(self.config, dict):
            self.config_obj = CRMDataSourceConfig.model_validate(self.config)
        else:
            raise ValueError("config must be a list or dict")
    
    def load_documents(self, db_session: Optional[Session] = None) -> Generator[Document, None, None]:
        """Load crm documents from CRM database, each entity forms a Document."""
        # Use provided session or create a new session
        session_created = False
        if db_session is None:
            db_session = next(get_db_session())
            session_created = True
            
        try:
            # Load opportunity documents
            if self.config_obj.include_opportunities:
                yield from self._load_opportunity_documents(db_session)
                    
            # Load account documents
            if self.config_obj.include_accounts:
                yield from self._load_account_documents(db_session)
                    
            # Load contact documents
            if self.config_obj.include_contacts:
                yield from self._load_contact_documents(db_session)
                    
        finally:
            # Close the session we created ourselves
            if session_created and db_session:
                db_session.close()
    
    def _load_entity_documents(
        self,
        db_session: Session,
        entity_model,
        filter_condition: Optional[str],
        max_count: Optional[int],
        entity_name: str,
        get_related_data_func,
        create_document_func
    ) -> Generator[Document, None, None]:
        """Generic method to load documents from a database table."""
        # Get the total number of entities to batch process
        count_query = select(func.count(entity_model.id))
        if filter_condition:
            count_query = count_query.where(text(filter_condition))
        
        total_count = db_session.exec(count_query).one()
        logger.info(f"Found {total_count} {entity_name}(s) matching filter")
        
        # Apply count limit
        if max_count:
            total_count = min(total_count, max_count)
        
        # Batch process
        for offset in range(0, total_count, self.config_obj.batch_size):
            limit = min(self.config_obj.batch_size, total_count - offset)
            logger.info(f"Processing {entity_name}(s) batch: offset={offset}, limit={limit}")
            
            # Get the entities of current batch
            query = select(entity_model)
            if filter_condition:
                query = query.where(text(filter_condition))
            query = query.offset(offset).limit(limit)
            
            entities = db_session.exec(query).all()
            
            # Get related data as needed
            related_data = get_related_data_func(db_session, entities)
            
            # Create a Document for each entity
            for entity in entities:
                try:
                    # Create a Document using the provided function
                    document = create_document_func(entity, related_data)
                    yield document
                except Exception as e:
                    entity_id = getattr(entity, 'unique_id', 'unknown')
                    logger.error(f"Error processing {entity_name} {entity_id}: {str(e)}")
                    continue
 
    def _load_opportunity_documents(self, db_session: Session) -> Generator[Document, None, None]:
        """Load opportunity documents from database."""
        
        def get_related_data(session, opportunities):
            # No related data for opportunity
            return {}
           
        def create_document(opportunity, related_data):
            # Create and return the document
            return self._create_opportunity_document(opportunity)
        
        return self._load_entity_documents(
            db_session=db_session,
            entity_model=CRMOpportunity,
            filter_condition=self.config_obj.opportunity_filter,
            max_count=self.config_obj.max_count,
            entity_name="opportunity",
            get_related_data_func=get_related_data,
            create_document_func=create_document
        )

    def _load_account_documents(self, db_session: Session) -> Generator[Document, None, None]:
        """Load account documents from database."""
        
        def get_related_data(session, accounts):
            # No related data for account
            return {}
        
        def create_document(account, related_data):
            # Create and return the document
            return self._create_account_document(account)
        
        return self._load_entity_documents(
            db_session=db_session,
            entity_model=CRMAccount,
            filter_condition=self.config_obj.account_filter,
            max_count=self.config_obj.max_count,
            entity_name="account",
            get_related_data_func=get_related_data,
            create_document_func=create_document
        )
 
    def _load_contact_documents(self, db_session: Session) -> Generator[Document, None, None]:
        """Load contact documents from database."""
        
        def get_related_data(session, contacts):
            # No related data for contact
            return {}
        
        def create_document(contact, related_data):
            # Create and return the document
            return self._create_contact_document(contact)
        
        return self._load_entity_documents(
            db_session=db_session,
            entity_model=CRMContact,
            filter_condition=self.config_obj.contact_filter,
            max_count=self.config_obj.max_count,
            entity_name="contact",
            get_related_data_func=get_related_data,
            create_document_func=create_document
        )
     
    def _create_opportunity_document(self, opportunity) -> Document:
        """Create a Document object for a single opportunity."""
        # Create document content
        content = []
        content.extend(format_opportunity_info(opportunity))        
        content_str = "\n".join(content)
        
        # Create metadata
        metadata = self._create_metadata(opportunity, CrmDataType.OPPORTUNITY)
        
        doc_datetime = datetime.now()
        # Create Document object
        return Document(
            name=f"{opportunity.opportunity_name or '未命名商机'}",
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            source_uri="crm_database/opportunities",
            created_at=doc_datetime,
            updated_at=doc_datetime,
            last_modified_at=doc_datetime,
            meta=metadata,
        )

    def _create_account_document(self, account) -> Document:
        """Create a Document object for a single account."""
        # Create document content
        content = []
        content.extend(format_account_info(account))
        content_str = "\n".join(content)
        
        # Create metadata
        metadata = self._create_metadata(account, CrmDataType.ACCOUNT)
        
        doc_datetime = datetime.now()
        # Create Document object
        return Document(
            name=f"{account.customer_name or account.unique_id or '未命名客户'}",
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            source_uri="crm_database/accounts",
            created_at=doc_datetime,
            updated_at=doc_datetime,
            last_modified_at=doc_datetime,
            meta=metadata,
        )       

    def _create_contact_document(self, contact) -> Document:
        """Create a Document object for a single contact."""
        # Create document content
        content = []
        content.extend(format_contact_info(contact))
        content_str = "\n".join(content)
        
        # Create metadata
        metadata = self._create_metadata(contact, CrmDataType.CONTACT)

        doc_datetime = datetime.now()
        # Create Document object
        return Document(
            name=f"{contact.name or contact.unique_id or '未命名联系人'}",
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            source_uri="crm_database/contacts",
            created_at=doc_datetime,
            updated_at=doc_datetime,
            last_modified_at=doc_datetime,
            meta=metadata,
        )

    def _create_metadata(self, entity, data_type, related_entities=None) -> Dict:
        """Create document metadata based on entity type."""
        metadata = {"category": DocumentCategory.CRM, "crm_data_type": data_type}
        
        if entity:
            _, entity_columns = get_column_comments_and_names(type(entity))
            
            # Exclude unnecessary fields
            exclude_fields = {"id"}
            
            # Basic fields processing
            for field_name in entity_columns:
                if field_name in exclude_fields:
                    continue
                    
                value = getattr(entity, field_name, None)
                if value is None:
                    continue
                
                # Special handling for date time fields
                if isinstance(value, (datetime, date)):
                    metadata[field_name] = value.isoformat()
                # Process numeric fields
                elif isinstance(value, (int, float)):
                    try:
                        metadata[field_name] = float(value) if value else 0.0
                    except (ValueError, TypeError):
                        metadata[field_name] = 0.0
                # Process boolean fields
                elif field_name.startswith("is_") or field_name.startswith("has_"):
                    metadata[field_name] = value == '是'
                # Regular field processing
                else:
                    metadata[field_name] = str(value) if value else ""
                
        # Ensure all values are JSON serializable
        for key, value in list(metadata.items()):
            if isinstance(value, (datetime, date)):
                metadata[key] = value.isoformat()
            elif not (isinstance(value, (str, int, float, bool, list, dict)) or value is None):
                metadata[key] = str(value)
        
        return metadata