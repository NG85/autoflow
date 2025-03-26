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
        opportunity_id = meta.get("unique_id")
        if not opportunity_id:
            logger.warning("Missing unique_id as opportunity_id in metadata, skipping graph construction")
            return
               
        # 初始化CRM数据结构
        opportunity_data = {"opportunity_id": opportunity_id}
        account_data = None
        contacts_data = []
        
        for key, value in meta.items():
            # Filter out the contacts list, this will be handled separately
            if key != "contacts" and value is not None:
                opportunity_data[key] = value
        
        # 处理客户数据
        account_data = meta.get("account")
        if not account_data:
            logger.warning("No account data found in metadata, relationships cannot be created")
            return
        
        account_id = account_data.get("account_id")
        if not account_id:
            logger.warning("No account_id found in metadata, relationships cannot be created")
            return
            
        # 处理联系人数据
        contacts_list = meta.get("contacts", [])
        if contacts_list and isinstance(contacts_list, list):
            # 处理联系人列表
            for contact in contacts_list:
                if not isinstance(contact, dict):
                    continue
                    
                # 确保联系人数据包含 account_id 以建立关系
                if "account_id" not in contact and account_id:
                    contact["account_id"] = account_id
                    
                # 确保联系人数据有ID
                if "contact_id" not in contact:
                    continue
                    
                contacts_data.append(contact)
        # 处理单个联系人情况
        elif meta.get("contact_id"):
            contact_data = {"contact_id": meta.get("contact_id")}
            # 提取所有contact_前缀字段
            for key, value in meta.items():
                if key.startswith("contact_") and key != "contact_id" and value is not None:
                    contact_data[key] = value
                    
            # 确保联系人与客户关联
            if "account_id" not in contact_data and account_id:
                contact_data["account_id"] = account_id
                
            contacts_data.append(contact_data)
            
        # 数据验证
        if not opportunity_data:
            logger.warning("No valid opportunity data found, skipping graph construction")
            return
            
        logger.info(f"Creating graph for opportunity {opportunity_id} with {len(contacts_data)} contacts")
                   
        # 2. 创建实体、关系
        builder = CRMKnowledgeGraphBuilder()
        entities_data, relationships_data = builder.build_graph_from_document_data(
            opportunity_data=opportunity_data,
            account_data=account_data,
            contacts_data=contacts_data,
            document_id=document_id,
            chunk_id=chunk_id,
            meta=meta
        )
                  
        # 3. 创建 DataFrame
        entities_df = pd.DataFrame(entities_data)
        relationships_df = pd.DataFrame(relationships_data)
        
        # 4. 保存实体和关系
        if not entities_df.empty and not relationships_df.empty:
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