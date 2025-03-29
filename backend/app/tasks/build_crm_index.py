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
                if key not in ["crm_data_type"] and value is not None:
                    opportunity_data[key] = value
            
            # 如果商机有客户关联信息，创建一个简单的Account数据（从商机信息中提取）
            if opportunity_data.get("customer_name"):
               secondary_data = {
                   "account_id": opportunity_data.get("customer_id"),
                   "account_name": opportunity_data.get("customer_name"),
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
            
            # 构建一个简单的Account数据（从客户信息中提取）
            secondary_data = {
                "account_id": account_id,
                "account_name": account_data.get("customer_name") or account_data.get("account_name"),
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

            # 如果没有客户ID但有客户名称，使用客户名称作为ID
            if not customer_id and customer_name:
                customer_id = customer_name
                contact_data["customer_id"] = customer_id

            # 如果联系人有客户信息，创建一个简单的Account数据（从联系人信息中提取）
            if customer_name:
                secondary_data = {
                    "account_id": customer_id or customer_name,
                    "account_name": customer_name,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                }
            
            primary_data = contact_data            
            logger.info(f"Creating graph for contact {contact_id} with customer {customer_name}")
            
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