import traceback
from uuid import UUID
from celery.utils.log import get_task_logger
import pandas as pd
from app.celery import app as celery_app
from sqlmodel import Session
from app.core.db import engine
from app.models import (
    Document as DBDocument,
    CrmKgIndexStatus,
)
from app.rag.indices.knowledge_graph.crm.builder import CRMKnowledgeGraphBuilder
from app.models.chunk import get_kb_chunk_model
from app.repositories import knowledge_base_repo
from app.repositories.chunk import ChunkRepo
from app.models.document import DocumentCategory
from app.rag.knowledge_base.index_store import get_kb_tidb_graph_store
from app.models.enums import GraphType
from app.rag.types import CrmDataType

logger = get_task_logger(__name__)
    

@celery_app.task(bind=True)
def build_crm_graph_index_for_document(
    self,
    knowledge_base_id: int,
    document_id: int,
):
    logger.info(f"Start building CRM graph index from document #{document_id}")
     
    with Session(engine, expire_on_commit=False) as session:
        kb = knowledge_base_repo.must_get(session, knowledge_base_id)
        graph_store = get_kb_tidb_graph_store(session, kb, graph_type=GraphType.crm)
        
        # Check document.
        db_document = session.get(DBDocument, document_id)
        if db_document is None:
            logger.error(f"CRM document #{document_id} is not found")
            return

        if db_document.get_metadata().category != DocumentCategory.CRM:
            logger.error(f"Document #{document_id} is not crm category")
            return
        
        # Extract CRM data from document metadata
        meta = db_document.meta or {}
            
    with Session(engine, expire_on_commit=False) as session:
        # Check chunk.
        chunk_repo = ChunkRepo(get_kb_chunk_model(kb))
        chunks = chunk_repo.get_document_chunks(session, document_id)
        
        if chunks is None or len(chunks) == 0:
            logger.error(f"Chunk for CRM document #{document_id} is not found")
            return

        if len(chunks) > 1:
            logger.error(f"Multiple chunks found for CRM document #{document_id}")
            return
        
        chunk = chunks[0]
        chunk_id = str(chunk.id) if isinstance(chunk.id, UUID) else chunk.id
        if chunk.crm_index_status not in (
            CrmKgIndexStatus.PENDING,
            CrmKgIndexStatus.NOT_STARTED,
        ):
            logger.info(f"CRM chunk #{chunk.id} is not in pending state")
            return
            
        chunk.crm_index_status = CrmKgIndexStatus.RUNNING
        session.add(chunk)
        session.commit()
               
    try:
        # 获取CRM数据类型，确定是哪种实体类型的文档
        crm_data_type = meta.get("crm_data_type")
        if not crm_data_type:
            logger.warning("Missing crm_data_type in metadata, unable to determine entity type")
            return
            
        logger.info(f"Building graph for CRM entity type: {crm_data_type}")

        # 初始化CRM数据结构
        primary_data = {}
        secondary_data = {}
        
        opportunity_data = {}
        account_data = {}
        contact_data = {}
        order_data = {}
        payment_plan_data = {}
        opportunity_updates_data = {}
         
        # 根据不同的实体类型处理
        if crm_data_type == CrmDataType.OPPORTUNITY:
            # 获取商机ID
            opportunity_id = meta.get("unique_id")
            if not opportunity_id:
                logger.warning("Missing unique_id as opportunity_id in metadata, skipping graph construction")
                return
                
            # 构建商机数据
            opportunity_data = {"opportunity_id": opportunity_id}
            for key, value in meta.items():
                # 过滤销售活动记录相关字段，单独通过opportunity_updates来处理
                if key not in ["crm_data_type", "weekly_update", "todo_and_followup", "call_high_notes"] and value is not None:
                    opportunity_data[key] = value
            
            # 如果商机有客户/负责人关联信息，创建一个简单的Account/我方对接人数据（从商机信息中提取）
            if opportunity_data.get("customer_name") or opportunity_data.get("owner"):
                secondary_data = {
                    "account_id": opportunity_data.get("customer_id"),
                    "account_name": opportunity_data.get("customer_name"),
                    "internal_owner": opportunity_data.get("owner"),
                    "internal_department": opportunity_data.get("owner_main_department"),
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                }
                
            primary_data = opportunity_data
            logger.info(f"Creating graph for opportunity {opportunity_id} with account {opportunity_data.get('customer_name')}")
           
        elif crm_data_type == CrmDataType.ACCOUNT:
            # 获取客户ID
            account_id = meta.get("unique_id")
            if not account_id:
                logger.warning("Missing unique_id as account_id in metadata, skipping graph construction")
                return
                
            # 构建客户数据
            account_data = {"account_id": account_id}
            for key, value in meta.items():
                if key not in ["crm_data_type"] and value is not None:
                    account_data[key] = value
            
            # 如果客户有负责人信息，构建一个简单的我方对接人数据（从客户信息中提取）
            if account_data.get("person_in_charge"):
                secondary_data = {
                    "internal_owner": account_data.get("person_in_charge"),
                    "internal_department": account_data.get("department"),
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                }
            
            primary_data = account_data
            logger.info(f"Creating graph for account {account_id}")

        elif crm_data_type == CrmDataType.CONTACT:
            # 获取联系人ID
            contact_id = meta.get("unique_id")
            if not contact_id:
                logger.warning("Missing unique_id as contact_id in metadata, skipping graph construction")
                return
                
            # 构建联系人数据
            contact_data = {"contact_id": contact_id}
            for key, value in meta.items():
                if key not in ["crm_data_type"] and value is not None:
                    contact_data[key] = value
            
            customer_id = contact_data.get("customer_id") or contact_data.get("account_id")
            customer_name = contact_data.get("customer_name")

            # 如果联系人有客户信息，创建一个简单的Account数据（从联系人信息中提取）
            if customer_name:
                secondary_data = {
                    "account_id": customer_id,
                    "account_name": customer_name,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                }
            
            primary_data = contact_data            
            logger.info(f"Creating graph for contact {contact_id} with customer {customer_name}")
        
        elif crm_data_type == CrmDataType.ORDER:
            # 获取订单ID
            order_id = meta.get("unique_id")
            if not order_id:
                logger.warning("Missing unique_id as order_id in metadata, skipping graph construction")
                return
            
            # 构建订单数据
            order_data = {"order_id": order_id}
            for key, value in meta.items():
                if key not in ["crm_data_type"] and value is not None:
                    order_data[key] = value
            
            # 如果订单有客户/商机关联信息，创建一个简单的Account/Opportunity/我方对接人数据（从订单信息中提取）
            if order_data.get("customer_name") or order_data.get("opportunity_name") or order_data.get("owner"):
               secondary_data = {
                   "account_id": order_data.get("customer_id"),
                   "account_name": order_data.get("customer_name"),
                   "opportunity_id": order_data.get("opportunity_id"),
                   "opportunity_name": order_data.get("opportunity_name"),
                   "internal_owner": order_data.get("owner"),
                   "internal_department": order_data.get("owner_department"),
                   "document_id": document_id,
                   "chunk_id": chunk_id,
                }
            primary_data = order_data
            logger.info(f"Creating graph for order {order_id} with account {order_data.get('customer_name')}")
           
        
        elif crm_data_type == CrmDataType.PAYMENTPLAN:
            # 获取回款计划ID
            payment_plan_id = meta.get("unique_id")
            if not payment_plan_id:
                logger.warning("Missing unique_id as payment_plan_id in metadata, skipping graph construction")
                return
            
            # 构建回款计划数据
            payment_plan_data = {"payment_plan_id": payment_plan_id}
            for key, value in meta.items():
                if key not in ["crm_data_type"] and value is not None:
                    payment_plan_data[key] = value
            
            # 如果回款计划有订单/我方对接人关联信息，创建一个简单的Order/我方对接人数据（从回款计划信息中提取）
            if payment_plan_data.get("order_id") or payment_plan_data.get("owner"):
               secondary_data = {
                   "order_id": payment_plan_data.get("order_id"),
                   "order_amount": payment_plan_data.get("order_amount"),
                   "internal_owner": payment_plan_data.get("owner"),
                   "internal_department": payment_plan_data.get("owner_department"),
                   "document_id": document_id,
                   "chunk_id": chunk_id,
                }
                
            primary_data = payment_plan_data
            logger.info(f"Creating graph for payment plan {payment_plan_id} with order {payment_plan_data.get('order_name')}")
           
        elif crm_data_type == CrmDataType.OPPORTUNITY_UPDATES:
            # 获取商机更新记录分组ID
            updates_group_id = meta.get("unique_id")
            if not updates_group_id:
                logger.warning("Missing unique_id as updates_group_id in metadata, skipping graph construction")
                return
            
            # 构建商机更新记录数据
            opportunity_updates_data = {"updates_group_id": updates_group_id}
            for key, value in meta.items():
                if key not in ["crm_data_type"] and value is not None:
                    opportunity_updates_data[key] = value
            
            if opportunity_updates_data.get("opportunity_name"):
                secondary_data = {
                    "opportunity_id": opportunity_updates_data.get("opportunity_id"),
                    "opportunity_name": opportunity_updates_data.get("opportunity_name"),
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                }

            primary_data = opportunity_updates_data
            logger.info(f"Creating graph for opportunity updates {updates_group_id} with opportunity {opportunity_updates_data.get('opportunity_name')}")
           
        else:
            logger.warning(f"Unknown crm_data_type: {crm_data_type}, skipping graph construction")
            return          
   
        # 数据验证 - 确保存在有效的实体数据
        if not primary_data:
            logger.warning("No valid CRM entity data found, skipping graph construction")
            return             
        # 2. 创建实体、关系
        builder = CRMKnowledgeGraphBuilder()
        
        logger.debug(f"primary_data: {primary_data}")
        logger.debug(f"secondary_data: {secondary_data}")
        entities_data, relationships_data = builder.build_graph_from_document_data(
            crm_data_type=crm_data_type,
            primary_data=primary_data,
            secondary_data=secondary_data,
            document_id=document_id,
            chunk_id=chunk_id,
            meta=meta
        )
                  
        # 3. 创建 DataFrame
        entities_df = pd.DataFrame(entities_data)
        if entities_df.empty:
            logger.warning(f"No entity data generated for CRM document #{document_id}, skipping graph construction")
            return
        
        relationships_df = pd.DataFrame(relationships_data)
        logger.debug(f"entities_df: {entities_df}")
        logger.debug(f"relationships_df: {relationships_df}")
        # 4. 保存实体和关系
        graph_store.save(chunk.id, entities_df, relationships_df)
                        
        with Session(engine) as session:
            chunk.crm_index_status = CrmKgIndexStatus.COMPLETED
            session.add(chunk)
            session.commit()
            logger.info(
                f"Built crm knowledge graph index for chunk #{chunk.id} successfully."
            )       
    except Exception:
        with Session(engine) as session:
            error_msg = traceback.format_exc()
            logger.error(
                f"Failed to build crm knowledge graph index for chunk #{chunk.id}",
                exc_info=True,
            )
            chunk.crm_index_status = CrmKgIndexStatus.FAILED
            chunk.crm_index_result = error_msg
            session.add(chunk)
            session.commit()