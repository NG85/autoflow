from copy import deepcopy
from typing import Any, Dict, List
from app.rag.indices.knowledge_graph.crm.entity import (
    AccountEntity,
    ContactEntity,
    InternalOwnerEntity,
    OpportunityEntity,
    OpportunityUpdatesEntity,
    OrderEntity,
    PaymentPlanEntity
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
        
        metadata = {}
        metadata["account_name"] = account_name
        if "account_id" in account_data:
            metadata["account_id"] = account_data["account_id"]

        return AccountEntity(
            id=None,
            name=account_name,
            description=f"客户{account_name}",
            metadata=metadata
        )

    def create_internal_owner_entity(self, data: Dict) -> List[InternalOwnerEntity]:
        """Create internal owner entity."""
        internal_owner = data.get("internal_owner")
        internal_department = data.get("internal_department")
        if not internal_owner:
            return []

        owners = internal_owner if isinstance(internal_owner, list) else [internal_owner]                    
        return [
            InternalOwnerEntity(
                id=None,
                name=owner,
                description=f"我方对接人{owner}" + (f", 主属部门{internal_department}" if internal_department else ""),
                metadata={
                    "internal_owner": owner,
                    "internal_department": internal_department,
                }
            )
            for owner in owners
            if owner != ''
        ]
                
    def create_account_detail_entity(self, account_data: Dict) -> AccountEntity:
        """Create account detail entity."""
        account_name = account_data.get("account_name") or account_data.get("customer_name")
        return AccountEntity(
            id=None,
            name=account_name,
            description=f"关于客户{account_name}的明细数据，包括客户行业、等级、合作状态等",
            metadata=account_data
        )
        
    def create_contact_entity(self, contact_data: Dict) -> ContactEntity:
        """Create contact entity."""
        contact_name = contact_data.get("contact_name") or contact_data.get("name")
        
        metadata = {}
        metadata["contact_name"] = contact_name
        if "contact_id" in contact_data:
            metadata["contact_id"] = contact_data["contact_id"]

        return ContactEntity(
            id=None,
            name=contact_name,
            description=f"联系人{contact_name}",
            metadata=metadata
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
        
        metadata = {}
        metadata["opportunity_name"] = opportunity_name
        if "opportunity_id" in opportunity_data:
            metadata["opportunity_id"] = opportunity_data["opportunity_id"]
            
        return OpportunityEntity(
            id=None,
            name=opportunity_name,
            description=f"商机{opportunity_name}",
            metadata=metadata
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
        
    def create_opportunity_updates_entity(self, opportunity_updates_data: Dict) -> OpportunityUpdatesEntity:
        """Create opportunity updates entity."""
        opportunity_name = opportunity_updates_data.get("opportunity_name")
        
        metadata = {}
        metadata["opportunity_name"] = opportunity_name
        if "opportunity_id" in opportunity_updates_data:
            metadata["opportunity_id"] = opportunity_updates_data["opportunity_id"]

        return OpportunityUpdatesEntity(
            id=None,
            name=f"商机{opportunity_name}的活动更新记录",
            description=f"关于商机{opportunity_name}的销售活动更新记录，包括活动更新类型及时间、下一步行动计划、关键干系人、成单概率等",
            metadata=metadata
        )

    def create_order_entity(self, order_data: Dict) -> OrderEntity:
        """Create order entity."""
        order_name = order_data.get("sales_order_number") or order_data.get("order_id")
        
        metadata = {}
        metadata["order_name"] = order_name
        if "order_id" in order_data:
            metadata["order_id"] = order_data["order_id"]
        if "order_amount" in order_data:
            metadata["order_amount"] = order_data["order_amount"]

        return OrderEntity(
            id=None,
            name=order_name,
            description=f"订单{order_name}",
            metadata=metadata
        )
        
    def create_order_detail_entity(self, order_data: Dict) -> OrderEntity:
        """Create order detail entity."""
        order_name = order_data.get("sales_order_number") or order_data.get("order_id")
        return OrderEntity(
            id=None,
            name=order_name,
            description=f"关于订单{order_name}的明细数据，包括订单金额、订单状态、订单类型等",
            metadata=order_data
        )
    
    # def create_payment_plan_entity(self, payment_plan_data: Dict) -> PaymentPlanEntity:
    #     """Create payment plan entity."""
    #     payment_plan_name = payment_plan_data.get("name") or payment_plan_data.get("payment_plan_id")
        
    #     metadata = {}
    #     if "payment_plan_id" in payment_plan_data:
    #         metadata["payment_plan_id"] = payment_plan_data["payment_plan_id"]
    #     if "unique_id" in payment_plan_data:
    #         metadata["unique_id"] = payment_plan_data["unique_id"]

    #     return PaymentPlanEntity(
    #         id=None,
    #         name=payment_plan_name, 
    #         description=f"回款计划{payment_plan_name}",
    #         metadata=metadata
    #     )
        
    def create_payment_plan_detail_entity(self, payment_plan_data: Dict) -> PaymentPlanEntity:
        """Create payment plan detail entity."""
        payment_plan_name = payment_plan_data.get("name") or payment_plan_data.get("payment_plan_id")
        return PaymentPlanEntity(
            id=None,
            name=payment_plan_name,
            description=f"关于回款计划{payment_plan_name}的明细数据，包括回款金额、回款时间、回款状态等",
            metadata=payment_plan_data
        )
        
    
    def build_graph_from_document_data(
        self,
        crm_data_type: CrmDataType,
        primary_data: Dict[str, Any],
        secondary_data: Dict[str, Any],
        document_id: str = None,
        chunk_id: str = None,
        meta: Dict[str, Any] = None
    ) -> tuple[List[Dict], List[Dict]]:
        """Build CRM knowledge graph from document data"""
        # 初始化实体和关系列表
        entities_data = []
        relationships_data = []
        
        if crm_data_type == CrmDataType.ACCOUNT:
            # 创建客户详情实体
            account_detail_entity = self.create_account_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": account_detail_entity.name,
                    "description": account_detail_entity.description,
                    "meta": account_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
            # 创建默认的客户关系
            relationships_data.append({
                "source_entity": account_detail_entity.name,
                "target_entity": account_detail_entity.name,
                "source_entity_description": account_detail_entity.description,
                "target_entity_description": account_detail_entity.description,
                "relationship_desc": f"客户{account_detail_entity.name}包含更多详细信息",
                "meta": {
                    **meta,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                    "relation_type": "HAS_DETAIL",
                    "crm_data_type": crm_data_type,
                    "source_type": CrmDataType.ACCOUNT,
                    "target_type": CrmDataType.ACCOUNT,
                    "unique_id": account_detail_entity.metadata.get("unique_id") or account_detail_entity.metadata.get("account_id")
                }
            })
            # 如果存在我方对接人数据，创建我方对接人实体
            if secondary_data and secondary_data.get("internal_owner"):
                internal_owner_entities = self.create_internal_owner_entity(secondary_data)
                for internal_owner_entity in internal_owner_entities:
                    entities_data.extend([
                        {
                            "name": internal_owner_entity.name,
                            "description": internal_owner_entity.description,
                            "meta": internal_owner_entity.metadata,
                            "graph_type": GraphType.crm
                        }
                    ])
                    relationships_data.append({
                        "source_entity": account_detail_entity.name,
                        "target_entity": internal_owner_entity.name,
                        "source_entity_description": account_detail_entity.description,
                        "target_entity_description": internal_owner_entity.description,
                        "relationship_desc": f"客户{account_detail_entity.name}由我方对接人：{internal_owner_entity.name}负责维护",
                        "meta": {
                            **meta,
                            "document_id": document_id,
                            "chunk_id": chunk_id,
                            "relation_type": "HANDLED_BY",
                            "crm_data_type": crm_data_type,
                            "source_type": CrmDataType.ACCOUNT,
                            "target_type": CrmDataType.INTERNAL_OWNER,
                            "unique_id": account_detail_entity.metadata.get("unique_id") or account_detail_entity.metadata.get("account_id")
                        }
                    })
                        
        elif crm_data_type == CrmDataType.CONTACT:
            # 创建联系人详情实体
            contact_detail_entity = self.create_contact_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": contact_detail_entity.name,
                    "description": contact_detail_entity.description,
                    "meta": contact_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
            # 创建默认的联系人关系
            relationships_data.append({
                "source_entity": contact_detail_entity.name,
                "target_entity": contact_detail_entity.name,
                "source_entity_description": contact_detail_entity.description,
                "target_entity_description": contact_detail_entity.description,
                "relationship_desc": f"联系人{contact_detail_entity.name}包含更多详细信息",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "HAS_DETAIL",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.CONTACT,
                        "target_type": CrmDataType.CONTACT,
                        "unique_id": contact_detail_entity.metadata.get("unique_id") or contact_detail_entity.metadata.get("contact_id")
                }
            })
            
            if secondary_data:
                # 如果客户实体存在，创建联系人详情-客户关系
                if secondary_data.get("account_name"):
                    account_entity = self.create_account_entity(secondary_data) 
                    entities_data.append({
                        "name": account_entity.name,
                        "description": account_entity.description,
                        "meta": account_entity.metadata,
                        "graph_type": GraphType.crm
                    })
                    relationships_data.append({
                        "source_entity": contact_detail_entity.name,
                        "target_entity": account_entity.name,
                        "source_entity_description": contact_detail_entity.description,
                        "target_entity_description": account_entity.description,
                        "relationship_desc": f"联系人{contact_detail_entity.name}属于客户{account_entity.name}",
                        "meta": {
                            **meta,
                            "document_id": document_id,
                            "chunk_id": chunk_id,
                            "relation_type": "BELONGS_TO",
                            "crm_data_type": crm_data_type,
                            "source_type": CrmDataType.CONTACT,
                            "target_type": CrmDataType.ACCOUNT,
                            "unique_id": contact_detail_entity.metadata.get("unique_id") or contact_detail_entity.metadata.get("contact_id")
                        }
                    })
                
                # 如果我方对接人实体存在，创建联系人详情-我方对接人关系
                if secondary_data.get("internal_owner"):
                    internal_owner_entities = self.create_internal_owner_entity(secondary_data)
                    for internal_owner_entity in internal_owner_entities:
                        entities_data.append({
                            "name": internal_owner_entity.name,
                            "description": internal_owner_entity.description,
                            "meta": internal_owner_entity.metadata,
                            "graph_type": GraphType.crm
                        })
                        relationships_data.append({
                            "source_entity": contact_detail_entity.name,
                            "target_entity": internal_owner_entity.name,    
                            "source_entity_description": contact_detail_entity.description,
                            "target_entity_description": internal_owner_entity.description,
                            "relationship_desc": f"联系人{contact_detail_entity.name}由我方对接人：{internal_owner_entity.name}负责对接",
                            "meta": {
                                **meta,
                                "document_id": document_id, 
                                "chunk_id": chunk_id,
                                "relation_type": "HANDLED_BY",
                                "crm_data_type": crm_data_type,
                                "source_type": CrmDataType.CONTACT,
                                "target_type": CrmDataType.INTERNAL_OWNER,
                                "unique_id": contact_detail_entity.metadata.get("unique_id") or contact_detail_entity.metadata.get("contact_id")
                            }
                        })

        elif crm_data_type == CrmDataType.OPPORTUNITY:
            # 创建商机详情实体
            opportunity_detail_entity = self.create_opportunity_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": opportunity_detail_entity.name,
                    "description": opportunity_detail_entity.description,
                    "meta": opportunity_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
            # 创建默认的商机关系
            relationships_data.append({
                    "source_entity": opportunity_detail_entity.name,
                    "target_entity": opportunity_detail_entity.name,
                    "source_entity_description": opportunity_detail_entity.description,
                    "target_entity_description": opportunity_detail_entity.description,
                    "relationship_desc": f"商机{opportunity_detail_entity.name}包含更多详细信息",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "HAS_DETAIL",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.OPPORTUNITY,
                        "target_type": CrmDataType.OPPORTUNITY,
                        "unique_id": opportunity_detail_entity.metadata.get("unique_id") or opportunity_detail_entity.metadata.get("opportunity_id")
                    }
                }
            )  
            
            if secondary_data:
                # 如果客户实体存在，创建商机-客户关系
                if secondary_data.get("account_name"):
                    account_entity = self.create_account_entity(secondary_data)
                    entities_data.append({
                        "name": account_entity.name,
                        "description": account_entity.description,
                        "meta": account_entity.metadata,
                        "graph_type": GraphType.crm
                    })
                    relationships_data.append({
                        "source_entity": opportunity_detail_entity.name,
                        "target_entity": account_entity.name,
                        "source_entity_description": opportunity_detail_entity.description,
                        "target_entity_description": account_entity.description,
                        "relationship_desc": f"商机{opportunity_detail_entity.name}来自于客户{account_entity.name}",
                        "meta": {
                            **meta,
                            "document_id": document_id,
                            "chunk_id": chunk_id,
                            "relation_type": "GENERATED_FROM",
                            "crm_data_type": crm_data_type,
                            "source_type": CrmDataType.OPPORTUNITY,
                            "target_type": CrmDataType.ACCOUNT,
                            "unique_id": opportunity_detail_entity.metadata.get("unique_id") or opportunity_detail_entity.metadata.get("opportunity_id")
                        }
                    })
                # 如果我方对接人实体存在，创建商机-我方对接人关系
                if secondary_data.get("internal_owner"):
                    internal_owner_entities = self.create_internal_owner_entity(secondary_data)
                    for internal_owner_entity in internal_owner_entities:
                        entities_data.append({
                            "name": internal_owner_entity.name,
                            "description": internal_owner_entity.description,
                            "meta": internal_owner_entity.metadata,
                            "graph_type": GraphType.crm
                        })
                        relationships_data.append({
                            "source_entity": opportunity_detail_entity.name,
                            "target_entity": internal_owner_entity.name,
                            "source_entity_description": opportunity_detail_entity.description,
                            "target_entity_description": internal_owner_entity.description,
                            "relationship_desc": f"商机{opportunity_detail_entity.name}由我方对接人：{internal_owner_entity.name}负责跟进",
                            "meta": {
                                **meta,
                                "document_id": document_id,
                                "chunk_id": chunk_id,
                                "relation_type": "HANDLED_BY",
                                "crm_data_type": crm_data_type,
                                "source_type": CrmDataType.OPPORTUNITY,
                                "target_type": CrmDataType.INTERNAL_OWNER,
                                "unique_id": opportunity_detail_entity.metadata.get("unique_id") or opportunity_detail_entity.metadata.get("opportunity_id")
                            }
                        })
                    
        elif crm_data_type == CrmDataType.ORDER:
            # 创建订单详情实体
            order_detail_entity = self.create_order_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": order_detail_entity.name,
                    "description": order_detail_entity.description,
                    "meta": order_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
            # 创建默认的订单关系
            relationships_data.append({
                "source_entity": order_detail_entity.name,
                "target_entity": order_detail_entity.name,
                "source_entity_description": order_detail_entity.description,
                "target_entity_description": order_detail_entity.description,
                "relationship_desc": f"订单{order_detail_entity.name}包含更多详细信息",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "HAS_DETAIL",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.ORDER,
                        "target_type": CrmDataType.ORDER,
                        "unique_id": order_detail_entity.metadata.get("unique_id") or order_detail_entity.metadata.get("sales_order_number")
                    }
            })
            if secondary_data:
                # # 如果客户实体存在，创建订单-客户关系
                # if secondary_data.get("account_name"):
                #     account_entity = self.create_account_entity(secondary_data)
                #     entities_data.append({
                #         "name": account_entity.name,
                #         "description": account_entity.description,
                #         "meta": account_entity.metadata,
                #         "graph_type": GraphType.crm
                #     })
                #     relationships_data.append({
                #         "source_entity": order_detail_entity.name,
                #         "target_entity": account_entity.name,
                #         "source_entity_description": order_detail_entity.description,
                #         "target_entity_description": account_entity.description,
                #         "relationship_desc": f"订单{order_detail_entity.name}来自于客户{account_entity.name}",
                #         "meta": {
                #             **meta,
                #             "document_id": document_id,
                #             "chunk_id": chunk_id,
                #             "relation_type": "GENERATED_FROM",
                #             "crm_data_type": crm_data_type,
                #             "source_type": CrmDataType.ORDER,
                #             "target_type": CrmDataType.ACCOUNT,
                #             "unique_id": order_detail_entity.metadata.get("unique_id") or order_detail_entity.metadata.get("sales_order_number")
                #         }
                #     })
                # 如果商机实体存在，创建订单-商机关系
                if secondary_data.get("opportunity_name"):
                    opportunity_entity = self.create_opportunity_entity(secondary_data)
                    entities_data.append({
                        "name": opportunity_entity.name,
                        "description": opportunity_entity.description,
                        "meta": opportunity_entity.metadata,
                        "graph_type": GraphType.crm
                    })
                    relationships_data.append({
                        "source_entity": order_detail_entity.name,
                        "target_entity": opportunity_entity.name,
                        "source_entity_description": order_detail_entity.description,
                        "target_entity_description": opportunity_entity.description,
                        "relationship_desc": f"订单{order_detail_entity.name}来自于商机{opportunity_entity.name}",
                        "meta": {
                            **meta,
                            "document_id": document_id,
                            "chunk_id": chunk_id,
                            "relation_type": "GENERATED_FROM",
                            "crm_data_type": crm_data_type,
                            "source_type": CrmDataType.ORDER,
                            "target_type": CrmDataType.OPPORTUNITY,
                            "unique_id": order_detail_entity.metadata.get("unique_id") or order_detail_entity.metadata.get("sales_order_number")
                        }
                    })
                # 如果我方对接人实体存在，创建订单-我方对接人关系
                if secondary_data.get("internal_owner"):
                    internal_owner_entities = self.create_internal_owner_entity(secondary_data)
                    for internal_owner_entity in internal_owner_entities:
                        entities_data.append({
                            "name": internal_owner_entity.name,
                            "description": internal_owner_entity.description,
                            "meta": internal_owner_entity.metadata,
                            "graph_type": GraphType.crm
                        })
                        relationships_data.append({
                            "source_entity": order_detail_entity.name,
                            "target_entity": internal_owner_entity.name,
                            "source_entity_description": order_detail_entity.description,
                            "target_entity_description": internal_owner_entity.description,
                            "relationship_desc": f"订单{order_detail_entity.name}由我方对接人：{internal_owner_entity.name}负责签订",
                            "meta": {
                                **meta,
                                "document_id": document_id,
                                "chunk_id": chunk_id,
                                "relation_type": "HANDLED_BY",
                                "crm_data_type": crm_data_type,
                                "source_type": CrmDataType.ORDER,
                                "target_type": CrmDataType.INTERNAL_OWNER,
                                "unique_id": order_detail_entity.metadata.get("unique_id") or order_detail_entity.metadata.get("sales_order_number")
                            }
                        })
                

        elif crm_data_type == CrmDataType.PAYMENTPLAN:
            # 创建回款计划详情实体
            payment_plan_detail_entity = self.create_payment_plan_detail_entity(primary_data)
            entities_data.extend([
                {
                    "name": payment_plan_detail_entity.name,
                    "description": payment_plan_detail_entity.description,
                    "meta": payment_plan_detail_entity.metadata,
                    "graph_type": GraphType.crm
                }
            ])
            # 创建默认的回款计划关系
            relationships_data.append({
                "source_entity": payment_plan_detail_entity.name,
                "target_entity": payment_plan_detail_entity.name,
                "source_entity_description": payment_plan_detail_entity.description,
                "target_entity_description": payment_plan_detail_entity.description,
                "relationship_desc": f"回款计划{payment_plan_detail_entity.name}包含更多详细信息",
                "meta": {
                    **meta,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                    "relation_type": "HAS_DETAIL",
                    "crm_data_type": crm_data_type,
                    "source_type": CrmDataType.PAYMENTPLAN,
                    "target_type": CrmDataType.PAYMENTPLAN,
                    "unique_id": payment_plan_detail_entity.metadata.get("unique_id") or payment_plan_detail_entity.metadata.get("name")
                }
            })
            
            if secondary_data:
                # 如果订单实体存在，创建回款计划-订单关系
                if secondary_data.get("order_name"):
                    order_entity = self.create_order_entity(secondary_data)
                    entities_data.append({
                        "name": order_entity.name,
                        "description": order_entity.description,
                        "meta": order_entity.metadata,
                        "graph_type": GraphType.crm
                    })
                    relationships_data.append({
                        "source_entity": payment_plan_detail_entity.name,
                        "target_entity": order_entity.name,
                        "source_entity_description": payment_plan_detail_entity.description,
                        "target_entity_description": order_entity.description,
                        "relationship_desc": f"回款计划{payment_plan_detail_entity.name}属于订单{order_entity.name}",
                        "meta": {
                            **meta,
                            "document_id": document_id,
                            "chunk_id": chunk_id,
                            "relation_type": "BELONGS_TO",
                            "crm_data_type": crm_data_type,
                            "source_type": CrmDataType.PAYMENTPLAN,
                            "target_type": CrmDataType.ORDER,
                            "unique_id": payment_plan_detail_entity.metadata.get("unique_id") or payment_plan_detail_entity.metadata.get("name")
                        }
                    })
                    
                # 如果我方对接人实体存在，创建回款计划-我方对接人关系
                if secondary_data.get("internal_owner"):
                    internal_owner_entities = self.create_internal_owner_entity(secondary_data)
                    for internal_owner_entity in internal_owner_entities:
                        entities_data.append({
                            "name": internal_owner_entity.name,
                            "description": internal_owner_entity.description,
                            "meta": internal_owner_entity.metadata,
                            "graph_type": GraphType.crm
                        })
                        relationships_data.append({
                            "source_entity": payment_plan_detail_entity.name,
                            "target_entity": internal_owner_entity.name,
                            "source_entity_description": payment_plan_detail_entity.description,
                            "target_entity_description": internal_owner_entity.description,
                            "relationship_desc": f"回款计划{payment_plan_detail_entity.name}由我方对接人：{internal_owner_entity.name}负责追款",
                            "meta": {
                                **meta,
                                "document_id": document_id,
                                "chunk_id": chunk_id,
                                "relation_type": "HANDLED_BY",
                                "crm_data_type": crm_data_type,
                                "source_type": CrmDataType.PAYMENTPLAN,
                                "target_type": CrmDataType.INTERNAL_OWNER,
                                "unique_id": payment_plan_detail_entity.metadata.get("unique_id") or payment_plan_detail_entity.metadata.get("name")
                            }
                        })
        elif crm_data_type == CrmDataType.OPPORTUNITY_UPDATES:            
            # 创建商机-商机更新记录关系
            if secondary_data:
                opportunity_entity = self.create_opportunity_entity(secondary_data)
                opportunity_updates_detail_entity = self.create_opportunity_updates_entity(primary_data)
                entities_data.extend([
                    {
                        "name": opportunity_entity.name,
                        "description": opportunity_entity.description,
                        "meta": opportunity_entity.metadata,
                        "graph_type": GraphType.crm
                    },
                    {
                        "name": opportunity_updates_detail_entity.name,
                        "description": opportunity_updates_detail_entity.description,
                        "meta": opportunity_updates_detail_entity.metadata,
                        "graph_type": GraphType.crm
                    }
                ])
                relationships_data.append({
                    "source_entity": opportunity_entity.name,
                    "target_entity": opportunity_updates_detail_entity.name,
                    "source_entity_description": opportunity_entity.description,
                    "target_entity_description": opportunity_updates_detail_entity.description,
                    "relationship_desc": f"商机{opportunity_entity.name}包含很多详细的销售活动记录",
                    "meta": {
                        **meta,
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "relation_type": "HAS_DETAIL",
                        "crm_data_type": crm_data_type,
                        "source_type": CrmDataType.OPPORTUNITY,
                        "target_type": CrmDataType.OPPORTUNITY_UPDATES,
                        "unique_id": opportunity_entity.metadata.get("unique_id" or opportunity_entity.metadata.get("opportunity_id"))
                    }
                })      
        return entities_data, relationships_data