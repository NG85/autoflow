import logging
from typing import List, Optional, Tuple
import dspy
import pandas as pd
from dspy import Predict
from llama_index.core.schema import BaseNode

from app.rag.indices.knowledge_graph.extractor import SimpleGraphExtractor, get_relation_metadata_from_node
from app.rag.indices.knowledge_graph.schema import EntityCovariateInput, EntityCovariateOutput, KnowledgeGraph
from app.models.enums import GraphType

logger = logging.getLogger(__name__)

class ExtractPlaybookTriplet(dspy.Signature):
    """Carefully analyze the provided legal case text to identify legal entities and their relationships.
    
    Follow these Step-by-Step Analysis:

    1. Extract Key Entities:
      First, identify significant entities from the text:
        * Case: Legal case information
          Examples:
          - "张三诉李四劳动合同纠纷案"
          - "王五与某公司劳动争议案"
        * Court: Court information
          Examples:
          - "北京市海淀区人民法院"
          - "上海市第一中级人民法院"
        * Region: Geographic location
          Examples:
          - "北京市海淀区"
          - "上海市"
        * Party: Litigants and related parties
          Examples:
          - "张三（原告）"
          - "某公司（被告）"
        * Claim: Claims made by parties
          Examples:
          - "要求支付工资差额"
          - "要求解除劳动合同"
        * Fact: Key facts of the case
          Examples:
          - "2023年1月1日签订劳动合同"
          - "2023年6月1日被解除劳动合同"
        * Issue: Legal issues in dispute
          Examples:
          - "是否存在违法解除劳动合同"
          - "是否应当支付经济补偿金"
        * Viewpoint: Parties' arguments
          Examples:
          - "原告主张公司违法解除劳动合同"
          - "被告主张解除劳动合同合法"
        * JudgmentOpinion: Court's reasoning
          Examples:
          - "法院认为公司解除劳动合同违法"
          - "法院支持原告的诉讼请求"

      Important Classification Rules:
        - Case entities must contain title, filing date, and judgment date
        - Court entities must contain full name and level
        - Region entities must contain name
        - Party entities must contain name, role, and type
        - Claim entities must contain description
        - Fact entities must contain description and occurrence date
        - Issue entities must contain description
        - Viewpoint entities must contain side, content, and judgment
        - JudgmentOpinion entities must contain content
        - Only extract entities that are explicitly mentioned in the text
        - Do not infer or make assumptions about missing information
        - Each entity must have clear evidence in the source text
            
    2. Establish Relationships:
      Valid Relationship Patterns:
        1. Case is heard by Court
        2. Court is located in Region
        3. Case involves Party
        4. Party makes Claim
        5. Case contains Fact
        6. Fact relates to Issue
        7. Issue has Viewpoint
        8. JudgmentOpinion decides Issue
      
      Required Elements for Each Relationship Type:
      A. "Case is heard by Court":
        Must include:
        - Court's role in the case
        - Case handling process
        Example: "该案由北京市海淀区人民法院立案审理并作出判决"
      
      B. "Court is located in Region":
        Must include:
        - Geographic jurisdiction
        - Administrative level
        Example: "北京市海淀区人民法院位于北京市海淀区，属于基层人民法院"
      
      C. "Case involves Party":
        Must include:
        - Party's role in the case
        - Party's relationship to the case
        Example: "张三作为原告向法院提起诉讼，某公司作为被告应诉"
      
      D. "Party makes Claim":
        Must include:
        - Specific claim content
        - Legal basis
        Example: "原告主张被告支付工资差额，依据《劳动合同法》第38条"
      
      E. "Case contains Fact":
        Must include:
        - Fact's relevance to the case
        - Temporal sequence
        Example: "2023年1月1日，双方签订劳动合同，约定月工资5000元"
      
      F. "Fact relates to Issue":
        Must include:
        - Fact's connection to the issue
        - Legal significance
        Example: "劳动合同的签订时间与工资约定是判断是否存在违法解除的重要事实"
      
      G. "Issue has Viewpoint":
        Must include:
        - Party's position
        - Supporting arguments
        Example: "原告认为公司未提前通知即解除劳动合同构成违法解除"
      
      H. "JudgmentOpinion decides Issue":
        Must include:
        - Court's determination
        - Legal reasoning
        Example: "法院认为公司解除劳动合同未履行法定程序，构成违法解除"
    
      Critical Rules for Relationships:
        - Each relationship must be explicitly stated in the text
        - Both source and target entities must exist and be valid
        - Must use exact entity names in relationships
        - Do not infer or make assumptions about relationships
        - Relationships must follow the defined patterns
        - No reverse relationships
        - No relationships between same entity types
          
    3. Quality Guidelines:
      Basic Entity Information:
        - Each entity MUST have:
          * name: Clear, specific identifier (must be from source text)
          * description: Detailed description in complete sentences (must be based on source text)
          * metadata.topic: Must be exactly one of: "case", "court", "region", "party", "claim", "fact", "issue", "viewpoint", or "judgment_opinion"
       
      Relationship Rules:
        - Follow defined relationship patterns
        - Each relationship must be explicitly stated
        - Both source and target entities must be valid
        - Relationship descriptions must be specific and verifiable
        - Must use exact entity names in relationships
        - Only extract relationships that are explicitly mentioned in the text

    Please only response in JSON format:
    {
        "entities": [
            {
                "name": "entity name (specific and meaningful)",
                "description": "detailed description in complete sentences",
                "metadata": {
                    "topic": "case|court|region|party|claim|fact|issue|viewpoint|judgment_opinion"
                }
            }
        ],
        "relationships": [
            {
                "source_entity": "source entity name",
                "target_entity": "target entity name",
                "relationship_desc": "comprehensive description including all required elements"
            }
        ]
    }
    """

    text = dspy.InputField(desc="a paragraph of text to extract sales related entities and relationships to form a knowledge graph")
    knowledge: KnowledgeGraph = dspy.OutputField(desc="Graph representation of the sales knowledge extracted from the text.")

class ExtractPlaybookCovariate(dspy.Signature):
    """Please carefully review the provided text and entities list. Extract detailed metadata for each entity based on its type.
    
    Required metadata structure by entity type:

    1. Case entities:
        {
            "topic": "case",  # Must be first field and keep unchanged from input
            "title": "case title",  # Required
            "dateFiled": "filing date",  # Required, format: YYYY-MM-DD
            "dateJudged": "judgment date"  # Required, format: YYYY-MM-DD
        }

    2. Court entities:
        {
            "topic": "court",  # Must be first field and keep unchanged from input
            "name": "full court name",  # Required
            "level": "court level"  # Required, one of: "basic", "intermediate", "high"
        }
        
    3. Region entities:
        {
            "topic": "region",  # Must be first field and keep unchanged from input
            "name": "region name"  # Required
        }

    4. Party entities:
        {
            "topic": "party",  # Must be first field and keep unchanged from input
            "name": "party name",  # Required
            "role": "party role",  # Required, one of: "plaintiff", "defendant", "third_party"
            "type": "party type"  # Required, one of: "worker", "employer", "other"
        }

    5. Claim entities:
        {
            "topic": "claim",  # Must be first field and keep unchanged from input
            "description": "claim content"  # Required
        }

    6. Fact entities:
        {
            "topic": "fact",  # Must be first field and keep unchanged from input
            "description": "fact description",  # Required
            "dateOccurred": "fact occurrence date"  # Required, format: YYYY-MM-DD
        }

    7. Issue entities:
        {
            "topic": "issue",  # Must be first field and keep unchanged from input
            "description": "issue description"  # Required
        }

    8. Viewpoint entities:
        {
            "topic": "viewpoint",  # Must be first field and keep unchanged from input
            "side": "party side",  # Required, one of: "plaintiff", "defendant"
            "content": "viewpoint content",  # Required
            "judgment": "judgment result"  # Required, one of: "win", "lose"
        }

    9. JudgmentOpinion entities:
        {
            "topic": "judgment_opinion",  # Must be first field and keep unchanged from input
            "content": "court's reasoning"  # Required
        }

    Requirements:
    1. Field Requirements:
       - Topic field must be first and keep the value from input entity unchanged
       - All required fields must be present and non-empty
       - Only extract fields that are explicitly mentioned in the source text
       - Do not infer or make assumptions about missing information
       - Empty or null values are not allowed for any field
    
    2. Data Quality:
       - All values must be specific and verifiable in the source text
       - Use consistent terminology across all metadata
       - Dates must be in YYYY-MM-DD format
       - Enumerated values must match exactly with the allowed options
    
    3. Format Rules:
       - String values must be properly formatted and meaningful
       - Field names must exactly match the schema definition
       - Maintain consistent data types as specified
       - All required fields must be present
       - No additional fields beyond those specified
    
    Please only response in JSON format.
    """
    
    text = dspy.InputField(desc="a paragraph of text to extract covariates to claim the entities.")
    entities: List[EntityCovariateInput] = dspy.InputField(desc="List of entities identified in the text")
    covariates: List[EntityCovariateOutput] = dspy.OutputField(desc="Detailed metadata for each entity")


class PlaybookExtractor(SimpleGraphExtractor):
    """Extractor for sales playbook knowledge graph, using customized prompts for playbook entities and relationships"""
    def __init__(self, dspy_lm: dspy.LM, complied_extract_program_path: Optional[str] = None):
        super().__init__(dspy_lm, complied_extract_program_path, GraphType.playbook)
      
        # Create playbook extraction with proper template assignment
        self.extract_prog.prog_graph = Predict(ExtractPlaybookTriplet)
        self.extract_prog.prog_covariates = Predict(ExtractPlaybookCovariate)

          
    def _get_competitor_info(self, node: BaseNode) -> Optional[dict]:
        """Extract competitor information from document metadata"""
        metadata = node.metadata or {}
        if metadata.get("doc_owner") == "competitor":
            return {
                "name": metadata.get("product_name"),
                "company": metadata.get("company_name"),
                "category": metadata.get("product_category")
            }
        return None

    def _create_competitor_feature_relationship(
        self,
        competitor_entity: dict,
        feature: dict,
        base_metadata: dict
    ) -> dict:
        """Create an enhanced competitor-feature relationship."""
        # Extract feature benefits for richer description
        feature_benefits = feature.get("meta", {}).get("benefits", [])
        benefits_text = ", ".join(feature_benefits) if feature_benefits else "no specific benefits listed"
        
        # Create richer relationship description
        relationship_desc = (
            f"Competitor product {competitor_entity['name']} by {competitor_entity['meta']['company']} "
            f"provides feature: {feature['name']}. "
            f"Benefits include: {benefits_text}"
        )
        
        # Enhance metadata with relationship-specific info
        relationship_metadata = {
            **base_metadata,
            "relationship_type": "competitor_feature",
            "competitor_category": competitor_entity["meta"]["category"],
            "feature_source": competitor_entity["name"]
        }
  
        return {
            "source_entity": competitor_entity["name"],
            "source_entity_description": competitor_entity["description"],
            "target_entity": feature["name"],
            "target_entity_description": feature["description"],
            "relationship_desc": relationship_desc,
            "meta": relationship_metadata
        }      
      
    def extract(
        self,
        text: str,
        node: BaseNode,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Extract entities and relationships from text with document metadata context"""
        competitor_info = self._get_competitor_info(node)        
        entities_df, rel_df = super().extract(text, node)
  
        if competitor_info:
            logger.info(f"Competitor raw info: {competitor_info}")
            
            # 1. Create competitor entity
            competitor_entity = {
                "name": competitor_info["name"],
                "description": f"Competitor product {competitor_info['name']} by {competitor_info['company']}",
                "meta": {
                    "topic": "competitor",
                    "name": competitor_info["name"],
                    "company": competitor_info["company"],
                    "category": competitor_info["category"]
                }
            }
            
            entities_df = pd.concat([
                entities_df,
                pd.DataFrame([competitor_entity])
            ], ignore_index=True)
            
            # 2. Ensure all entities have valid metadata
            entities_df["meta"] = entities_df["meta"].apply(
                lambda x: x if isinstance(x, dict) else {}
            )
            
            # 3. Mark competitor features
            feature_mask = entities_df["meta"].apply(
                lambda x: x.get("topic") == "feature"
            )
            if feature_mask.any():
                entities_df.loc[feature_mask, "meta"] = entities_df.loc[feature_mask, "meta"].apply(
                    lambda x: {**x, "source": competitor_info["name"]}
                )
            
                # 4. Add Competitor Product-Feature relationship
                feature_entities = entities_df[feature_mask]
                competitor_product_feature_relations = []
                metadata = get_relation_metadata_from_node(node)
                logger.info(f"Get relation metadata from node: {metadata}")
                for _, feature in feature_entities.iterrows():
                  competitor_product_feature_relations.append(
                    self._create_competitor_feature_relationship(
                      competitor_entity,
                      feature,
                      metadata
                  ))
                
                if competitor_product_feature_relations:
                    rel_df = pd.concat([
                        rel_df,
                        pd.DataFrame(competitor_product_feature_relations)
                    ], ignore_index=True)
        
        return entities_df, rel_df