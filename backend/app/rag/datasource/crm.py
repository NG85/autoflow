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
from app.models.crm_orders import CRMOrder
from app.models.crm_payment_plans import CRMPaymentPlan
from app.models.crm_sales_activities import CRMSalesActivities
from sqlalchemy import text
from app.core.db import get_db_session
from app.types import MimeTypes
from app.rag.datasource.crm_format import(
    format_account_info,
    format_contact_info,
    format_opportunity_info,
    format_order_info,
    format_payment_plan_info
)
from app.rag.datasource.crm_to_file import save_crm_to_file
from app.models.document import DocumentCategory
from app.rag.types import CrmDataType

logger = logging.getLogger(__name__)


class CRMDataSourceConfig(BaseModel):
    """Config for CRM data source"""
    # include_accounts: Optional[bool] = False
    # include_contacts: Optional[bool] = True
    # include_opportunities: Optional[bool] = True
    # include_orders: Optional[bool] = True
    # include_payment_plans: Optional[bool] = True
    account_filter: Optional[str] = None
    # contact_filter: Optional[str] = None
    opportunity_filter: Optional[str] = None
    # order_filter: Optional[str] = None
    # payment_plan_filter: Optional[str] = None
    max_count: Optional[int] = None
    batch_size: int = 100
    increment_filter: Optional[str] = None

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
            # if self.config_obj.include_opportunities:
            yield from self._load_opportunity_documents(db_session)
                    
            # # Load account documents
            # if self.config_obj.include_accounts:
            yield from self._load_account_documents(db_session)
                    
            # # Load contact documents
            # if self.config_obj.include_contacts:
            #     yield from self._load_contact_documents(db_session)

            # # Load order documents
            # if self.config_obj.include_orders:
            #     yield from self._load_order_documents(db_session)
                    
            # # Load payment plan documents
            # if self.config_obj.include_payment_plans:
            #     yield from self._load_payment_plan_documents(db_session)
                
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
            
            # Create a Document for each entity
            for entity in entities:
                try:
                    # Get related data as needed
                    related_data = get_related_data_func(db_session, entity.unique_id)
                    # Create a Document using the provided function
                    document = create_document_func(entity, related_data)
                    yield document
                except Exception as e:
                    entity_id = getattr(entity, 'unique_id', 'unknown')
                    logger.error(f"Error processing {entity_name} {entity_id}: {str(e)}")
                    continue

     
    def _load_grouped_entity_documents(
        self,
        db_session: Session,
        entity_model,
        filter_condition: Optional[str],
        max_count: Optional[int],
        entity_name: str,
        get_related_data_func,
        create_document_func
    ) -> Generator[Document, None, None]:
        """Generic method to load documents from a database table, grouped by a specific field."""
        # Get the total number of entities to batch process
        count_query = select(func.count(entity_model.opportunity_id.distinct()))
        if filter_condition:
            count_query = count_query.where(text(filter_condition))
        
        total_count = db_session.exec(count_query).one()
        logger.info(f"Found {total_count} unique grouped {entity_name}(s) matching filter")
        
        # Apply count limit
        if max_count:
            total_count = min(total_count, max_count)
        
        # Batch process
        for offset in range(0, total_count, self.config_obj.batch_size):
            limit = min(self.config_obj.batch_size, total_count - offset)
            logger.info(f"Processing {entity_name}(s) batch: offset={offset}, limit={limit}")
            
            # Get the entities of current batch
            query = select(entity_model.opportunity_id).distinct()
            if filter_condition:
                query = query.where(text(filter_condition))
            query = query.offset(offset).limit(limit)
            
            opportunity_ids = db_session.exec(query).all()
            
            for opportunity_id in opportunity_ids:
                # Get related data for each opportunity
                related_data = get_related_data_func(db_session, opportunity_id, filter_condition)
                
                # Create a Document for each opportunity
                try:
                    document = create_document_func(opportunity_id, related_data)
                    yield document
                except Exception as e:
                    logger.error(f"Error processing {entity_name} {opportunity_id}: {str(e)}")
                    continue
                
    def _get_incremental_opportunity_ids(self, db_session: Session, increment_filter: str, opportunity_filter: Optional[str]):
        """Get all opportunity_ids that have changed in the primary table and sales activities table."""
        main_ids = set()
        sales_ids = set()
        main_query = select(CRMOpportunity.unique_id).where(text(increment_filter))
        if opportunity_filter:
            main_query = main_query.where(text(opportunity_filter))
        main_ids = set(row[0] for row in db_session.exec(main_query).all() if row and row[0])
        # Extended table: only use incremental filter
        sales_query = select(CRMSalesActivities.opportunity_id).where(text(increment_filter))
        sales_ids = set(row[0] for row in db_session.exec(sales_query).all() if row and row[0])
        all_ids = main_ids | sales_ids
        return list(all_ids)

    def _load_opportunity_documents(self, db_session: Session) -> Generator[Document, None, None]:
        """Load opportunity documents from database, support incremental synchronization."""
        increment_filter = getattr(self.config_obj, "increment_filter", None)
        opportunity_filter = getattr(self.config_obj, "opportunity_filter", None)
        def get_related_data(session, opportunity_id):
            # Get related orders (opportunity vs order is 1:1 relationship)
            orders_query = select(CRMOrder).filter(CRMOrder.opportunity_id==opportunity_id)
            orders = db_session.exec(orders_query).all()
            # Get related sales activities for each opportunity (opportunity vs sales activities is 1:N relationship)
            sales_activities_query = select(CRMSalesActivities).filter(CRMSalesActivities.opportunity_id==opportunity_id)
            sales_activities = db_session.exec(sales_activities_query).all()
            # Get related payment plans for each order (order vs paymentplan is 1:N relationship)
            orders_with_payment_plans = []
            total_payment_plans = 0
            for order in orders:
                payment_plans_query = select(CRMPaymentPlan).filter(
                    CRMPaymentPlan.order_id==order.unique_id,
                    (CRMPaymentPlan.is_deleted.is_(False) | CRMPaymentPlan.is_deleted.is_(None))
                )
                order_payment_plans = db_session.exec(payment_plans_query).all()
                orders_with_payment_plans.append({
                    "order": order,
                    "payment_plans": order_payment_plans
                })
                total_payment_plans += len(order_payment_plans)
            logger.info(f"Found opportunity {opportunity_id} with {len(orders)} orders, {total_payment_plans} payment plans, and {len(sales_activities)} sales activities")
            return {
                "orders_with_payment_plans": orders_with_payment_plans,
                "sales_activities": sales_activities
            }
           
        def create_document(opportunity, related_data):
            # Create and return the document
            return self._create_opportunity_document(opportunity, related_data)
        
        if increment_filter:
            # Incremental mode: primary table uses increment_filter AND opportunity_filter, extended table only uses increment_filter
            opportunity_ids = self._get_incremental_opportunity_ids(db_session, increment_filter, opportunity_filter)
            logger.info(f"Incremental synchronization, involving {len(opportunity_ids)} opportunities")
            # max_count 限制
            if self.config_obj.max_count:
                opportunity_ids = opportunity_ids[:self.config_obj.max_count]
            batch_size = self.config_obj.batch_size or 100
            for offset in range(0, len(opportunity_ids), batch_size):
                batch_ids = opportunity_ids[offset:offset+batch_size]
                for opportunity_id in batch_ids:
                    opportunity = db_session.exec(
                        select(CRMOpportunity).where(CRMOpportunity.unique_id == opportunity_id)
                    ).first()
                    if not opportunity:
                        continue
                    related_data = get_related_data(db_session, opportunity_id)
                    document = self._create_opportunity_document(opportunity, related_data)
                    yield document
        else:
            # Full mode: only use opportunity_filter on primary table
            yield from self._load_entity_documents(
                db_session=db_session,
                entity_model=CRMOpportunity,
                filter_condition=opportunity_filter,
                max_count=self.config_obj.max_count,
                entity_name="opportunity",
                get_related_data_func=get_related_data,
                create_document_func=create_document
            )

    def _get_incremental_account_ids(self, db_session: Session, increment_filter: str, account_filter: Optional[str]):
        """Get all account_ids that have changed in the primary table and sales activities table."""
        main_ids = set()
        sales_ids = set()
        main_query = select(CRMAccount.unique_id).where(text(increment_filter))
        if account_filter:
            main_query = main_query.where(text(account_filter))
        main_ids = set(row[0] for row in db_session.exec(main_query).all() if row and row[0])
        # Only activities not linked to any opportunity
        sales_query = select(CRMSalesActivities.account_id).where(
            text(increment_filter),
            (CRMSalesActivities.opportunity_id == None) | (CRMSalesActivities.opportunity_id == ""),
            (CRMSalesActivities.opportunity_name == None) | (CRMSalesActivities.opportunity_name == "")
        )
        sales_ids = set(row[0] for row in db_session.exec(sales_query).all() if row and row[0])
        all_ids = main_ids | sales_ids
        return list(all_ids)

    def _load_account_documents(self, db_session: Session) -> Generator[Document, None, None]:
        """Load account documents from database, only associate contacts and sales activities. 支持增量同步。"""
        increment_filter = getattr(self.config_obj, "increment_filter", None)
        account_filter = getattr(self.config_obj, "account_filter", None)

        def get_related_data(session, account_id):
            # # 1. Get all contacts under the account
            # contacts_query = select(CRMContact).filter(CRMContact.customer_id == account_id)
            # contacts = db_session.exec(contacts_query).all()
            # 2. Get all sales activities under the account, only those not linked to any opportunity
            sales_activities_query = select(CRMSalesActivities).filter(
                CRMSalesActivities.account_id == account_id,
                (CRMSalesActivities.opportunity_id == None) | (CRMSalesActivities.opportunity_id == ""),
                (CRMSalesActivities.opportunity_name == None) | (CRMSalesActivities.opportunity_name == "")
            )
            sales_activities = db_session.exec(sales_activities_query).all()
            return {
                # "contacts": contacts,
                "sales_activities": sales_activities
            }

        def create_document(account, related_data):
            return self._create_account_document(account, related_data)

        if increment_filter:
            account_ids = self._get_incremental_account_ids(db_session, increment_filter, account_filter)
            logger.info(f"Incremental synchronization, involving {len(account_ids)} accounts")
            # max_count 限制
            if self.config_obj.max_count:
                account_ids = account_ids[:self.config_obj.max_count]
            batch_size = self.config_obj.batch_size or 100
            for offset in range(0, len(account_ids), batch_size):
                batch_ids = account_ids[offset:offset+batch_size]
                for account_id in batch_ids:
                    account = db_session.exec(
                        select(CRMAccount).where(CRMAccount.unique_id == account_id)
                    ).first()
                    if not account:
                        continue
                    related_data = get_related_data(db_session, account_id)
                    document = self._create_account_document(account, related_data)
                    yield document
        else:
            # Full mode: only use account_filter
            yield from self._load_entity_documents(
                db_session=db_session,
                entity_model=CRMAccount,
                filter_condition=account_filter,
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
        
    def _load_order_documents(self, db_session: Session) -> Generator[Document, None, None]:
        """Load order documents from database."""
        
        def get_related_data(session, orders):
            # No related data for order
            return {}
        
        def create_document(order, related_data):
            # Create and return the document
            return self._create_order_document(order)
        
        return self._load_entity_documents(
            db_session=db_session,
            entity_model=CRMOrder,
            filter_condition=self.config_obj.order_filter,
            max_count=self.config_obj.max_count,
            entity_name="order",
            get_related_data_func=get_related_data,
            create_document_func=create_document
        )
    
    def _load_payment_plan_documents(self, db_session: Session) -> Generator[Document, None, None]:
        """Load payment plan documents from database."""
        
        def get_related_data(session, payment_plans):
            # No related data for payment plan
            return {}
        
        def create_document(payment_plan, related_data):
            # Create and return the document
            return self._create_payment_plan_document(payment_plan)
        
        return self._load_entity_documents(
            db_session=db_session,
            entity_model=CRMPaymentPlan,
            filter_condition=self.config_obj.payment_plan_filter,
            max_count=self.config_obj.max_count,
            entity_name="payment_plan",
            get_related_data_func=get_related_data,
            create_document_func=create_document
        )
     
    def _create_opportunity_document(self, opportunity, related_data) -> Document:
        """Create a Document object for a single opportunity."""
        # Create document content
        content = []
        content.extend(format_opportunity_info(opportunity, related_data))        
        content_str = "\n".join(content)
        
        # Create metadata
        metadata = self._create_metadata(opportunity, CrmDataType.OPPORTUNITY)
        
        doc_datetime = datetime.now()
        upload = save_crm_to_file("opportunity", opportunity, content_str, doc_datetime, metadata)
        logger.info(f"Created opportunity document {upload.id} with metadata {metadata}")
        # Create Document object
        return Document(
            name=upload.name if upload else f"{getattr(opportunity, 'opportunity_name', '未具名商机')}_{getattr(opportunity, 'unique_id')}.md",
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            file_id=upload.id,
            source_uri=upload.path if upload else f"crm/{getattr(opportunity, 'unique_id')}.md",
            created_at=doc_datetime,
            updated_at=doc_datetime,
            last_modified_at=doc_datetime,
            meta=metadata,
        )
        
    def _create_account_document(self, account, related_data=None) -> Document:
        """Create a Document object for a single account, with related data (contacts, sales activities only)."""
        # Create document content
        content = []
        content.extend(format_account_info(account, related_data))
        content_str = "\n".join(content)
        
        # Create metadata
        metadata = self._create_metadata(account, CrmDataType.ACCOUNT)
        
        doc_datetime = datetime.now()
        upload = save_crm_to_file("customer", account, content_str, doc_datetime, metadata)
        logger.info(f"Created account document {upload.id} with metadata {metadata}")
        # Create Document object
        return Document(
            name=upload.name if upload else f"{getattr(account, 'customer_name', '未具名客户')}_{getattr(account, 'unique_id')}.md",
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            file_id=upload.id,
            source_uri=upload.path if upload else f"crm/{getattr(account, 'unique_id')}.md",
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
    
    def _create_order_document(self, order) -> Document:
        """Create a document object for a single order."""
         # Create document content
        content = []
        content.extend(format_order_info(order))
        content_str = "\n".join(content)
        
        # Create metadata
        metadata = self._create_metadata(order, CrmDataType.ORDER)

        doc_datetime = datetime.now()
        # Create Document object
        return Document(
            name=f"{order.sales_order_number or order.unique_id or '未命名订单'}",
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            source_uri="crm_database/order",
            created_at=doc_datetime,
            updated_at=doc_datetime,
            last_modified_at=doc_datetime,
            meta=metadata,
        )
        
    def _create_payment_plan_document(self, payment_plan) -> Document:
        """Create a document object for a single payment plan."""
         # Create document content
        content = []
        content.extend(format_payment_plan_info(payment_plan))
        content_str = "\n".join(content)
        
        # Create metadata
        metadata = self._create_metadata(payment_plan, CrmDataType.PAYMENTPLAN)

        doc_datetime = datetime.now()
        # Create Document object
        return Document(
            name=f"{payment_plan.name or payment_plan.unique_id or '未命名回款计划'}",
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            source_uri="crm_database/payment_plan",
            created_at=doc_datetime,
            updated_at=doc_datetime,
            last_modified_at=doc_datetime,
            meta=metadata,
        )

    def _create_metadata(self, entity, data_type) -> Dict:
        """Create document metadata based on entity type."""
        metadata = {"category": DocumentCategory.CRM, "crm_data_type": data_type}
        
        if entity:
            # Define the key fields to retain
            # key_fields = {"unique_id", "account_id", "account_name", "customer_id", "customer_name", "customer_level", "industry",
            #               "opportunity_id", "opportunity_name", "forecast_type", "opportunity_stage", "stage_status", "expected_closing_date",
            #               "sales_order_number", "order_id", "name", "plan_payment_status",
            #               "person_in_charge", "department", "owner", "owner_department", "owner_main_department",
            #               "responsible_person", "responsible_department"}
            
            # for field_name in key_fields:
            #     if hasattr(entity, field_name):
            #         value = getattr(entity, field_name, None)
            #         if value is None:
            #             continue
                    
            #         metadata[field_name] = value if isinstance(value, str) else str(value) if value else ""

            value = getattr(entity, "unique_id", None)
            metadata["unique_id"] = value if isinstance(value, str) else str(value) if value else ""
        
        return metadata