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
        account_name = account_data.get("account_name") or account_data.get("customer_name")
        return AccountEntity(
            id=None,
            name=account_name,
            description=f"客户{account_name}",
            metadata=account_data
        )
        
    def create_account_detail_entity(self, account_data: Dict) -> AccountEntity:
        """Create account detail entity."""
        account_name = account_data.get("account_name") or account_data.get("customer_name")
        return AccountEntity(
            id=None,
            name=account_name,
            description=f"关于客户{account_name}的明细数据，包括客户行业、负责人、合作状态等",
            metadata=account_data
        )
        
    def create_contact_entity(self, contact_data: Dict) -> ContactEntity:
        """Create contact entity."""
        contact_name = contact_data.get("contact_name") or contact_data.get("name")
        return ContactEntity(
            id=None,
            name=contact_name,
            description=f"联系人{contact_name}",
            metadata=contact_data
        )
        
    def create_contact_detail_entity(self, contact_data: Dict) -> ContactEntity:
        """Create contact detail entity."""
        contact_name = contact_data.get("contact_name") or contact_data.get("name")
        return ContactEntity(
            id=None,
            name=contact_name,
            description=f"关于联系人{contact_name}的明细数据，包括联系方式、所属部门、职位等",
            metadata=contact_data
        )
           
    def create_opportunity_entity(self, opportunity_data: Dict) -> OpportunityEntity:
        """Create opportunity entity."""
        opportunity_name = opportunity_data.get("opportunity_name")
        return OpportunityEntity(
            id=None,
            name=opportunity_name,
            description=f"商机{opportunity_name}",
            metadata=opportunity_data
        )
    
    def create_opportunity_detail_entity(self, opportunity_data: Dict) -> OpportunityEntity:
        """Create opportunity detail entity."""
        opportunity_name = opportunity_data.get("opportunity_name")
        return OpportunityEntity(
            id=None,
            name=opportunity_name,
            description=f"关于商机{opportunity_name}的明细数据，包括商机类型、商机阶段、服务类型、商机金额等",
            metadata=opportunity_data
        )
    
    def build_graph_from_document_data(
        self,
        crm_data_type: CrmDataType,
        primary_data: Dict[str, Any],
        secondary_data: Dict[str, Any] = None,
        document_id: str = None,
        chunk_id: str = None,
        meta: Dict[str, Any] = None
    ) -> tuple[List[Dict], List[Dict]]:
        """Build CRM knowledge graph from document data"""
        # 初始化实体和关系列表
        entities_data = []
        relationships_data = []
        
        if crm_data_type == CrmDataType.ACCOUNT:
            # 创建客户实体、客户详情实体
            account_entity = self.create_account_entity(secondary_data)
            account_detail_entity = self.create_account_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": account_entity.name,
                    "description": account_entity.description,
                    "meta": account_entity.metadata,
                    "graph_type": GraphType.crm
                },
                {
                    "name": account_detail_entity.name,
                    "description": account_detail_entity.description,
                    "meta": account_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
            # 创建客户关系
            relationships_data.append({
                "source_entity": account_entity.name,
                "target_entity": account_detail_entity.name,
                "source_entity_description": account_entity.description,
                "target_entity_description": account_detail_entity.description,
                "relationship_desc": f"客户{account_entity.name}包含更多详细信息",
                "meta": {
                    **meta,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                    "relation_type": "HAS_DETAIL",
                    "crm_data_type": crm_data_type,
                    "source_type": CrmDataType.ACCOUNT,
                    "target_type": CrmDataType.ACCOUNT,
                    "unique_id": account_entity.metadata.get("unique_id") or account_entity.metadata.get("account_id")
                }
            })
        elif crm_data_type == CrmDataType.CONTACT:
            # 创建联系人实体、联系人详情实体
            contact_entity = self.create_contact_entity(primary_data)
            contact_detail_entity = self.create_contact_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": contact_entity.name,
                    "description": contact_entity.description,
                    "meta": contact_entity.metadata,
                    "graph_type": GraphType.crm
                },
                {
                    "name": contact_detail_entity.name,
                    "description": contact_detail_entity.description,
                    "meta": contact_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
            # 创建联系人关系
            relationships_data.append({
                "source_entity": contact_entity.name,
                "target_entity": contact_detail_entity.name,
                "source_entity_description": contact_entity.description,
                "target_entity_description": contact_detail_entity.description,
                "relationship_desc": f"联系人{contact_entity.name}包含更多详细信息",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "HAS_DETAIL",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.CONTACT,
                        "target_type": CrmDataType.CONTACT,
                        "unique_id": contact_entity.metadata.get("unique_id") or contact_entity.metadata.get("contact_id")
                }
            })
            # 如果客户实体存在，创建客户实体、联系人-客户关系
            if secondary_data:
                account_entity = self.create_account_entity(secondary_data) 
                entities_data.append({
                    "name": account_entity.name,
                    "description": account_entity.description,
                    "meta": account_entity.metadata,
                    "graph_type": GraphType.crm
                })
                relationships_data.append({
                    "source_entity": contact_entity.name,
                    "target_entity": account_entity.name,
                    "source_entity_description": contact_entity.description,
                    "target_entity_description": account_entity.description,
                    "relationship_desc": f"联系人{contact_entity.name}属于客户{account_entity.name}",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "BELONGS_TO",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.CONTACT,
                        "target_type": CrmDataType.ACCOUNT,
                        "unique_id": contact_entity.metadata.get("unique_id") or contact_entity.metadata.get("contact_id")
                    }
                })
                
        elif crm_data_type == CrmDataType.OPPORTUNITY:
            # 创建商机实体、商机详情实体
            opportunity_entity = self.create_opportunity_entity(primary_data)
            opportunity_detail_entity = self.create_opportunity_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": opportunity_entity.name,
                    "description": opportunity_entity.description,
                    "meta": opportunity_entity.metadata,
                    "graph_type": GraphType.crm
                },
                {
                    "name": opportunity_detail_entity.name,
                    "description": opportunity_detail_entity.description,
                    "meta": opportunity_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
                
            relationships_data.append({
                    "source_entity": opportunity_entity.name,
                    "target_entity": opportunity_detail_entity.name,
                    "source_entity_description": opportunity_entity.description,
                    "target_entity_description": opportunity_detail_entity.description,
                    "relationship_desc": f"商机{opportunity_entity.name}包含更多详细信息",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "HAS_DETAIL",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.OPPORTUNITY,
                        "target_type": CrmDataType.OPPORTUNITY,
                        "unique_id": opportunity_entity.metadata.get("unique_id") or opportunity_entity.metadata.get("opportunity_id")
                    }
                }
            )
            
            # 如果客户实体存在，创建客户实体、商机-客户关系
            if secondary_data:
                account_entity = self.create_account_entity(secondary_data)
                entities_data.append({
                    "name": account_entity.name,
                    "description": account_entity.description,
                    "meta": account_entity.metadata,
                    "graph_type": GraphType.crm
                })
                relationships_data.append({
                    "source_entity": opportunity_entity.name,
                    "target_entity": account_entity.name,
                    "source_entity_description": opportunity_entity.description,
                    "target_entity_description": account_entity.description,
                    "relationship_desc": f"商机{opportunity_entity.name}属于客户{account_entity.name}",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "BELONGS_TO",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.OPPORTUNITY,
                        "target_type": CrmDataType.ACCOUNT,
                        "unique_id": opportunity_entity.metadata.get("unique_id") or opportunity_entity.metadata.get("opportunity_id")
                    }
                })
        
        return entities_data, relationships_data