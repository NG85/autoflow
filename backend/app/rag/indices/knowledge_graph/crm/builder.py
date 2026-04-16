import hashlib
from copy import deepcopy
from typing import Any, Dict, List, Optional
from app.rag.indices.knowledge_graph.crm.entity import (
    AccountEntity,
    ContactEntity,
    InternalOwnerEntity,
    OpportunityEntity,
    OrderEntity,
    PaymentPlanEntity,
    ReviewRiskProgressEntity,
    ReviewSessionEntity,
    ReviewSnapshotEntity,
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
        
    
    def create_review_session_entity(self, data: Dict) -> ReviewSessionEntity:
        session_name = data.get("session_name") or data.get("session_id") or data.get("unique_id", "")
        dept_name = data.get("department_name", "")
        period = data.get("period", "")
        metadata = {
            "session_id": data.get("session_id") or data.get("unique_id", ""),
            "department_id": data.get("department_id", ""),
            "department_name": dept_name,
            "period": period,
            "stage": data.get("stage", ""),
        }
        return ReviewSessionEntity(
            id=None,
            name=session_name,
            description=f"Review会议 {session_name}（{dept_name} {period}）",
            metadata=metadata,
        )

    def create_review_snapshot_entity(self, data: Dict) -> ReviewSnapshotEntity:
        opp_name = data.get("opportunity_name") or data.get("opportunity_id", "")
        period = data.get("snapshot_period", "")
        metadata = {
            "opportunity_id": data.get("opportunity_id", ""),
            "snapshot_period": period,
            "forecast_type": data.get("forecast_type", ""),
            "opportunity_stage": data.get("opportunity_stage", ""),
            "owner_id": data.get("owner_id", ""),
            "owner_name": data.get("owner_name", ""),
            "account_name": data.get("account_name", ""),
        }
        return ReviewSnapshotEntity(
            id=None,
            name=f"{opp_name}_{period}",
            description=f"商机快照 {opp_name}（{period}），预测状态 {data.get('forecast_type', '')}",
            metadata=metadata,
        )

    def create_review_risk_progress_entity(self, data: Dict) -> ReviewRiskProgressEntity:
        type_name = data.get("type_name", "")
        record_type = data.get("record_type", "")
        scope_type = data.get("scope_type", "")
        period = data.get("snapshot_period", "")
        label = "风险" if record_type == "RISK" else "进展" if record_type == "PROGRESS" else record_type
        metadata = {
            "record_type": record_type,
            "type_code": data.get("type_code", ""),
            "scope_type": scope_type,
            "scope_id": data.get("scope_id", ""),
            "snapshot_period": period,
            "calc_phase": data.get("calc_phase", ""),
            "severity": data.get("severity", ""),
        }
        if data.get("opportunity_id"):
            metadata["opportunity_id"] = data["opportunity_id"]
        return ReviewRiskProgressEntity(
            id=None,
            name=f"{type_name}_{period}",
            description=f"{label}: {type_name}（{scope_type}, {period}）",
            metadata=metadata,
        )

    def _extract_review_fact_lines(self, chunk_text: Optional[str]) -> List[str]:
        """Extract normalized fact lines from review chunk text."""
        text = (chunk_text or "").strip()
        if not text:
            return []
        out: List[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line.startswith("- "):
                continue
            # Keep only structured fact bullets to avoid noisy generic bullets.
            if "指标=" not in line:
                continue
            if ("范围类型=" not in line) and ("部门=" not in line) and ("负责销售=" not in line):
                continue
            out.append(line[2:].strip())
        return out

    def _parse_review_fact_kvs(self, fact_line: str) -> Dict[str, str]:
        """Parse key-value pairs from one fact line."""
        result: Dict[str, str] = {}
        for seg in fact_line.split("；"):
            s = seg.strip().rstrip("。")
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            key = k.strip()
            value = v.strip()
            if key:
                result[key] = value
        return result

    def _append_review_chunk_facts(
        self,
        relationships_data: List[Dict[str, Any]],
        *,
        source_entity_name: str,
        source_entity_description: str,
        rel_meta_base: Dict[str, Any],
        crm_data_type: CrmDataType,
        chunk_text: Optional[str],
    ) -> None:
        """Append chunk-level fact relations so each chunk can contribute distinct facts."""
        fact_lines = self._extract_review_fact_lines(chunk_text)
        for idx, fact_line in enumerate(fact_lines):
            parsed = self._parse_review_fact_kvs(fact_line)
            scope_type = parsed.get("范围类型", "")
            scope_name = parsed.get("范围名称", "")
            if not scope_type and "部门" in parsed:
                scope_type = "部门"
                scope_name = parsed.get("部门", "")
            if not scope_type and "负责销售" in parsed:
                scope_type = "负责人"
                scope_name = parsed.get("负责销售", "")
            metric_raw = parsed.get("指标", "")
            metric_key = ""
            if "(" in metric_raw and ")" in metric_raw:
                metric_key = metric_raw.rsplit("(", 1)[-1].rstrip(")").strip()
            fact_fingerprint = "|".join(
                [
                    str(rel_meta_base.get("session_id", "")),
                    str(rel_meta_base.get("snapshot_period", "")),
                    str(rel_meta_base.get("chunk_id", "")),
                    str(scope_type),
                    str(scope_name),
                    str(metric_key or metric_raw),
                    str(parsed.get("当前值", "")),
                    str(parsed.get("上期值", "")),
                    str(parsed.get("变化量", "")),
                    str(parsed.get("变化率", "")),
                ]
            )
            fact_hash = hashlib.sha1(fact_fingerprint.encode("utf-8")).hexdigest()
            relationships_data.append(
                {
                    "source_entity": source_entity_name,
                    "target_entity": source_entity_name,
                    "source_entity_description": source_entity_description,
                    "target_entity_description": source_entity_description,
                    "relationship_desc": fact_line,
                    "meta": {
                        **rel_meta_base,
                        "relation_type": "HAS_FACT",
                        "crm_data_type": crm_data_type,
                        "source_type": crm_data_type,
                        "target_type": crm_data_type,
                        "fact_index": idx,
                        "fact_scope_type": scope_type,
                        "fact_scope_name": scope_name,
                        "fact_metric": metric_raw,
                        "fact_metric_key": metric_key,
                        "fact_current_value": parsed.get("当前值", ""),
                        "fact_prev_value": parsed.get("上期值", ""),
                        "fact_delta": parsed.get("变化量", ""),
                        "fact_rate": parsed.get("变化率", ""),
                        "fact_hash": fact_hash,
                    },
                }
            )

    def build_graph_from_document_data(
        self,
        crm_data_type: CrmDataType,
        primary_data: Dict[str, Any],
        secondary_data: Dict[str, Any],
        document_id: str = None,
        chunk_id: str = None,
        meta: Dict[str, Any] = None,
        chunk_text: str = None,
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

        elif crm_data_type == CrmDataType.REVIEW_SESSION:
            session_entity = self.create_review_session_entity(primary_data)
            entities_data.append({
                "name": session_entity.name,
                "description": session_entity.description,
                "meta": session_entity.metadata,
                "graph_type": GraphType.crm,
            })
            rel_meta_base = {
                **meta,
                "document_id": document_id,
                "chunk_id": chunk_id,
                "crm_data_type": crm_data_type,
                "session_id": primary_data.get("session_id") or primary_data.get("unique_id", ""),
                "snapshot_period": primary_data.get("period", ""),
                "stage": primary_data.get("stage", "") or meta.get("stage", ""),
                "calc_phase": primary_data.get("calc_phase", "") or meta.get("calc_phase", ""),
                "unique_id": primary_data.get("session_id") or primary_data.get("unique_id", ""),
            }
            include_self_detail_relation = bool(
                (meta or {}).get("include_self_detail_relation", False)
            )
            if include_self_detail_relation:
                relationships_data.append({
                    "source_entity": session_entity.name,
                    "target_entity": session_entity.name,
                    "source_entity_description": session_entity.description,
                    "target_entity_description": session_entity.description,
                    "relationship_desc": f"Review会议{session_entity.name}包含更多详细信息",
                    "meta": {**rel_meta_base, "relation_type": "HAS_DETAIL",
                             "source_type": CrmDataType.REVIEW_SESSION,
                             "target_type": CrmDataType.REVIEW_SESSION},
                })
            # Session → Department
            dept_name = primary_data.get("department_name")
            is_primary_chunk_for_document = bool(
                (meta or {}).get("is_primary_chunk_for_document", True)
            )
            if dept_name and is_primary_chunk_for_document:
                dept_entity = AccountEntity(
                    id=None,
                    name=dept_name,
                    description=f"部门{dept_name}",
                    metadata={"department_id": primary_data.get("department_id", ""),
                              "department_name": dept_name},
                )
                entities_data.append({
                    "name": dept_entity.name,
                    "description": dept_entity.description,
                    "meta": dept_entity.metadata,
                    "graph_type": GraphType.crm,
                })
                relationships_data.append({
                    "source_entity": session_entity.name,
                    "target_entity": dept_entity.name,
                    "source_entity_description": session_entity.description,
                    "target_entity_description": dept_entity.description,
                    "relationship_desc": f"Review会议{session_entity.name}属于部门{dept_name}",
                    "meta": {**rel_meta_base, "relation_type": "BELONGS_TO",
                             "source_type": CrmDataType.REVIEW_SESSION,
                             "target_type": CrmDataType.DEPARTMENT},
                })
            self._append_review_chunk_facts(
                relationships_data,
                source_entity_name=session_entity.name,
                source_entity_description=session_entity.description,
                rel_meta_base=rel_meta_base,
                crm_data_type=crm_data_type,
                chunk_text=chunk_text,
            )

        elif crm_data_type == CrmDataType.REVIEW_SNAPSHOT:
            snapshot_entity = self.create_review_snapshot_entity(primary_data)
            entities_data.append({
                "name": snapshot_entity.name,
                "description": snapshot_entity.description,
                "meta": snapshot_entity.metadata,
                "graph_type": GraphType.crm,
            })
            rel_meta_base = {
                **meta,
                "document_id": document_id,
                "chunk_id": chunk_id,
                "crm_data_type": crm_data_type,
                "session_id": primary_data.get("session_id") or meta.get("session_id", ""),
                "snapshot_period": primary_data.get("snapshot_period", ""),
                "stage": primary_data.get("stage", "") or meta.get("stage", ""),
                "calc_phase": primary_data.get("calc_phase", "") or meta.get("calc_phase", ""),
                "unique_id": primary_data.get("unique_id", ""),
            }
            relationships_data.append({
                "source_entity": snapshot_entity.name,
                "target_entity": snapshot_entity.name,
                "source_entity_description": snapshot_entity.description,
                "target_entity_description": snapshot_entity.description,
                "relationship_desc": f"商机快照{snapshot_entity.name}包含更多详细信息",
                "meta": {**rel_meta_base, "relation_type": "HAS_DETAIL",
                         "source_type": CrmDataType.REVIEW_SNAPSHOT,
                         "target_type": CrmDataType.REVIEW_SNAPSHOT},
            })
            # Snapshot → Opportunity
            opp_name = primary_data.get("opportunity_name")
            opp_id = primary_data.get("opportunity_id")
            if opp_name or opp_id:
                opp_entity = self.create_opportunity_entity({
                    "opportunity_id": opp_id,
                    "opportunity_name": opp_name or opp_id,
                })
                entities_data.append({
                    "name": opp_entity.name,
                    "description": opp_entity.description,
                    "meta": opp_entity.metadata,
                    "graph_type": GraphType.crm,
                })
                relationships_data.append({
                    "source_entity": snapshot_entity.name,
                    "target_entity": opp_entity.name,
                    "source_entity_description": snapshot_entity.description,
                    "target_entity_description": opp_entity.description,
                    "relationship_desc": f"商机快照{snapshot_entity.name}是商机{opp_entity.name}的周期快照",
                    "meta": {**rel_meta_base, "relation_type": "SNAPSHOT_OF",
                             "source_type": CrmDataType.REVIEW_SNAPSHOT,
                             "target_type": CrmDataType.OPPORTUNITY},
                })
            # Snapshot → ReviewSession
            if secondary_data and secondary_data.get("session_id"):
                session_name = secondary_data.get("session_name") or secondary_data.get("session_id", "")
                session_entity = ReviewSessionEntity(
                    id=None,
                    name=session_name,
                    description=f"Review会议 {session_name}",
                    metadata={"session_id": secondary_data["session_id"]},
                )
                entities_data.append({
                    "name": session_entity.name,
                    "description": session_entity.description,
                    "meta": session_entity.metadata,
                    "graph_type": GraphType.crm,
                })
                relationships_data.append({
                    "source_entity": snapshot_entity.name,
                    "target_entity": session_entity.name,
                    "source_entity_description": snapshot_entity.description,
                    "target_entity_description": session_entity.description,
                    "relationship_desc": f"商机快照{snapshot_entity.name}属于Review会议{session_entity.name}",
                    "meta": {**rel_meta_base, "relation_type": "BELONGS_TO",
                             "source_type": CrmDataType.REVIEW_SNAPSHOT,
                             "target_type": CrmDataType.REVIEW_SESSION},
                })
            # Snapshot → Owner
            owner_name = primary_data.get("owner_name")
            if owner_name:
                owner_entities = self.create_internal_owner_entity({
                    "internal_owner": owner_name,
                    "internal_department": primary_data.get("owner_department_name", ""),
                })
                for owner_entity in owner_entities:
                    entities_data.append({
                        "name": owner_entity.name,
                        "description": owner_entity.description,
                        "meta": owner_entity.metadata,
                        "graph_type": GraphType.crm,
                    })
                    relationships_data.append({
                        "source_entity": snapshot_entity.name,
                        "target_entity": owner_entity.name,
                        "source_entity_description": snapshot_entity.description,
                        "target_entity_description": owner_entity.description,
                        "relationship_desc": f"商机快照{snapshot_entity.name}由{owner_entity.name}负责",
                        "meta": {**rel_meta_base, "relation_type": "HANDLED_BY",
                                 "source_type": CrmDataType.REVIEW_SNAPSHOT,
                                 "target_type": CrmDataType.INTERNAL_OWNER},
                    })
            self._append_review_chunk_facts(
                relationships_data,
                source_entity_name=snapshot_entity.name,
                source_entity_description=snapshot_entity.description,
                rel_meta_base=rel_meta_base,
                crm_data_type=crm_data_type,
                chunk_text=chunk_text,
            )

        elif crm_data_type == CrmDataType.REVIEW_RISK_PROGRESS:
            rp_entity = self.create_review_risk_progress_entity(primary_data)
            entities_data.append({
                "name": rp_entity.name,
                "description": rp_entity.description,
                "meta": rp_entity.metadata,
                "graph_type": GraphType.crm,
            })
            rel_meta_base = {
                **meta,
                "document_id": document_id,
                "chunk_id": chunk_id,
                "crm_data_type": crm_data_type,
                "session_id": primary_data.get("session_id") or meta.get("session_id", ""),
                "snapshot_period": primary_data.get("snapshot_period", ""),
                "calc_phase": primary_data.get("calc_phase", ""),
                "stage": primary_data.get("stage", "") or meta.get("stage", ""),
                "unique_id": primary_data.get("unique_id", ""),
            }
            relationships_data.append({
                "source_entity": rp_entity.name,
                "target_entity": rp_entity.name,
                "source_entity_description": rp_entity.description,
                "target_entity_description": rp_entity.description,
                "relationship_desc": f"{rp_entity.name}包含更多详细信息",
                "meta": {**rel_meta_base, "relation_type": "HAS_DETAIL",
                         "source_type": CrmDataType.REVIEW_RISK_PROGRESS,
                         "target_type": CrmDataType.REVIEW_RISK_PROGRESS},
            })
            # RiskProgress → ReviewSession
            if secondary_data and secondary_data.get("session_id"):
                session_name = secondary_data.get("session_name") or secondary_data.get("session_id", "")
                session_entity = ReviewSessionEntity(
                    id=None,
                    name=session_name,
                    description=f"Review会议 {session_name}",
                    metadata={"session_id": secondary_data["session_id"]},
                )
                entities_data.append({
                    "name": session_entity.name,
                    "description": session_entity.description,
                    "meta": session_entity.metadata,
                    "graph_type": GraphType.crm,
                })
                relationships_data.append({
                    "source_entity": rp_entity.name,
                    "target_entity": session_entity.name,
                    "source_entity_description": rp_entity.description,
                    "target_entity_description": session_entity.description,
                    "relationship_desc": f"{rp_entity.name}属于Review会议{session_entity.name}",
                    "meta": {**rel_meta_base, "relation_type": "BELONGS_TO",
                             "source_type": CrmDataType.REVIEW_RISK_PROGRESS,
                             "target_type": CrmDataType.REVIEW_SESSION},
                })
            # RiskProgress → Opportunity (if opportunity-level)
            opp_id = primary_data.get("opportunity_id")
            if opp_id:
                opp_entity = self.create_opportunity_entity({
                    "opportunity_id": opp_id,
                    "opportunity_name": opp_id,
                })
                entities_data.append({
                    "name": opp_entity.name,
                    "description": opp_entity.description,
                    "meta": opp_entity.metadata,
                    "graph_type": GraphType.crm,
                })
                relationships_data.append({
                    "source_entity": rp_entity.name,
                    "target_entity": opp_entity.name,
                    "source_entity_description": rp_entity.description,
                    "target_entity_description": opp_entity.description,
                    "relationship_desc": f"{rp_entity.name}关联商机{opp_entity.name}",
                    "meta": {**rel_meta_base, "relation_type": "DETECTED_IN",
                             "source_type": CrmDataType.REVIEW_RISK_PROGRESS,
                             "target_type": CrmDataType.OPPORTUNITY},
                })
            self._append_review_chunk_facts(
                relationships_data,
                source_entity_name=rp_entity.name,
                source_entity_description=rp_entity.description,
                rel_meta_base=rel_meta_base,
                crm_data_type=crm_data_type,
                chunk_text=chunk_text,
            )
    
        return entities_data, relationships_data