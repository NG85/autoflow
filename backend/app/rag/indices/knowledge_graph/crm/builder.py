from typing import Any, Dict, List
from app.rag.indices.knowledge_graph.crm.entity import (
    AccountEntity,
    ContactEntity,
    OpportunityEntity
)
from app.rag.types import CrmDataType
from app.models.enums import GraphType

class CRMKnowledgeGraphBuilder:
    """CRM knowledge graph builder."""
    def __init__(self):
        pass
           
    def create_account_entity(self, account_data: Dict) -> AccountEntity:
        """Create account entity."""
        account_name = account_data.get("account_name")
        industry = account_data.get("industry", "")
        customer_level = account_data.get("customer_level", "")
        return AccountEntity(
            id=None,
            name=account_name,
            description=f"{account_name}是我们在{industry}行业中的{customer_level}客户",
            metadata=account_data
        )
        
    def create_contact_entity(self, contact_data: Dict) -> ContactEntity:
        """Create contact entity."""
        contact_name = contact_data.get("contact_name")
        position = contact_data.get("position", "未知")
        department = contact_data.get("department", "未知")
        return ContactEntity(
            id=None,
            name=contact_name,
            description=f"联系人{contact_name}的岗位是{position}，所在部门{department}",
            metadata=contact_data
        )
           
    def create_opportunity_entity(self, opportunity_data: Dict) -> OpportunityEntity:
        """Create opportunity entity."""
        opportunity_name = opportunity_data.get("opportunity_name")
        opportunity_stage = opportunity_data.get("opportunity_stage", "未知")
        owner = opportunity_data.get("owner", "未知")
        return OpportunityEntity(
            id=None,
            name=opportunity_name,
            description=f"{opportunity_name}的商机阶段是{opportunity_stage}，负责人是{owner}",
            metadata=opportunity_data
        )
 
    def build_graph_from_document_data(
        self,
        opportunity_data: Dict[str, Any] = None,
        account_data: Dict[str, Any] = None, 
        contacts_data: List[Dict[str, Any]] = None,
        document_id: str = None,
        chunk_id: str = None,
        meta: Dict[str, Any] = None
    ) -> tuple[List[Dict], List[Dict]]:
        """
        Build CRM knowledge graph from document data
        
        Args:
            opportunity_data: Opportunity data
            account_data: Account data
            contacts_data: List of contact data
            document_id: Document ID
            chunk_id: Document chunk ID
            meta: Metadata
            
        Returns:
            (entities_data, relationships_data): Entity and Relationship data
        """
        entities_data = []
        relationships_data = []
        
        if not meta:
            meta = {}
        
        # 添加商机实体
        if opportunity_data:
            opportunity_entity = self.create_opportunity_entity(opportunity_data)
            entities_data.append({
                "name": opportunity_entity.name,
                "description": opportunity_entity.description,
                "meta": opportunity_entity.metadata,
                "graph_type": GraphType.crm
            })
            
        # 添加客户实体
        if account_data:
            account_entity = self.create_account_entity(account_data)
            entities_data.append({
                "name": account_entity.name,
                "description": account_entity.description,
                "meta": account_entity.metadata,
                "graph_type": GraphType.crm
            })
            
        # 处理联系人数据
        if contacts_data:
            # 创建联系人ID到联系人数据的映射
            contact_id_to_data = {}
            for contact in contacts_data:
                if "contact_id" in contact and contact["contact_id"]:
                    contact_id_to_data[contact["contact_id"]] = contact
            
            # 已处理的联系人ID集合，避免重复处理
            processed_contact_ids = set()
            # 存储联系人ID到实体的映射
            contact_entities_map = {}
            
            # 递归处理联系人及其上级链
            def process_contact_and_superiors(contact_id):
                # 如果已经处理过这个联系人，则直接返回
                if contact_id in processed_contact_ids:
                    return contact_entities_map.get(contact_id)
                
                # 如果没有这个联系人的数据，则返回None
                if contact_id not in contact_id_to_data:
                    return None
                
                # 标记为已处理
                processed_contact_ids.add(contact_id)
                
                contact_data = contact_id_to_data[contact_id]
                
                # 先处理上级（如果有）
                superior_entity = None
                if "direct_superior_id" in contact_data and contact_data["direct_superior_id"]:
                    superior_id = contact_data["direct_superior_id"]
                    # 递归处理上级
                    superior_entity = process_contact_and_superiors(superior_id)
                
                # 创建当前联系人实体
                contact_entity = self.create_contact_entity(contact_data)
                if contact_entity:
                    entities_data.append({
                        "name": contact_entity.name,
                        "description": contact_entity.description,
                        "meta": contact_entity.metadata,
                        "graph_type": GraphType.crm
                    })
                    
                    # 存储联系人实体映射
                    contact_entities_map[contact_id] = contact_entity
                    
                    # 如果有上级，建立关系
                    if superior_entity:
                        relationships_data.append({
                            "source_entity": contact_entity,
                            "target_entity": superior_entity,
                            "source_entity_description": contact_entity,
                            "target_entity_description": superior_entity,
                            "relationship_desc": f"联系人{contact_entity}的直属上级是{superior_entity}",
                            "meta": {
                                **meta,
                                "document_id": document_id,
                                "chunk_id": chunk_id,
                                "relation_type": "reports_to", 
                                "category": CrmDataType.CONTACT,
                                "source_type": CrmDataType.CONTACT,
                                "target_type": CrmDataType.CONTACT
                            },
                            "graph_type": GraphType.crm
                        })
                
                return contact_entity
            
            # 处理所有联系人
            for contact in contacts_data:
                if "contact_id" in contact and contact["contact_id"]:
                    process_contact_and_superiors(contact["contact_id"])
        
        # 添加客户与商机的关系
        if account_data and opportunity_data:
            opportunity_entity = opportunity_data.get("opportunity_name", f"商机 {opportunity_data.get('opportunity_id')}")
            account_entity = account_data.get("account_name", f"客户 {account_data.get('account_id')}")
            
            relationships_data.append({
                "source_entity": account_entity,
                "target_entity": opportunity_entity,
                "source_entity_description": account_entity,
                "target_entity_description": opportunity_entity,
                "relationship_desc": f"客户{account_entity}的商机包括{opportunity_entity}",
                "meta": {
                    **meta,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                    "relation_type": "has_opportunity",
                    "category": CrmDataType.OPPORTUNITY,
                    "source_type": CrmDataType.ACCOUNT,
                    "target_type": CrmDataType.OPPORTUNITY,
                    "opportunity_id": opportunity_data.get("opportunity_id", ""),
                    "account_id": account_data.get("account_id", "")
                },
                "graph_type": GraphType.crm
            })
        
        # 添加客户与联系人的关系
        if account_data and contacts_data:
            account_entity = account_data.get("account_name", f"客户 {account_data.get('account_id')}")
            
            for contact in contacts_data:
                contact_entity = contact.get("contact_name", f"联系人 {contact.get('contact_id')}")
                
                relationships_data.append({
                    "source_entity": account_entity,
                    "target_entity": contact_entity,
                    "source_entity_description": account_entity,
                    "target_entity_description": contact_entity,
                    "relationship_desc": f"客户{account_entity}的联系人之一是{contact_entity}",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "has_contact",
                        "category": CrmDataType.ACCOUNT,
                        "source_type": CrmDataType.ACCOUNT,
                        "target_type": CrmDataType.CONTACT,
                        "contact_id": contact.get("contact_id", ""),
                        "account_id": account_data.get("account_id", "")
                    },
                    "graph_type": GraphType.crm
                })
                
        return entities_data, relationships_data