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
    """Carefully analyze the provided text to identify sales related entities and their relationships.
    
    Follow these Step-by-Step Analysis:

    1. Extract Key Entities:
      First, identify significant entities from the text:
        * Personas (who): Organizations or departments that are potential customers
          Examples:
          - "Enterprise IT Department in Healthcare"
          - "Bank's Security Operations Team"
          - "Manufacturing Company's R&D Division"
          - "Marketing Manager in Financial Services"
        * Pain Points (what): Business challenges, problems, needs
        * Features (how): Solutions, capabilities, functionalities
        * Cases (proof): Customer success cases and implementation scenarios

      Important Classification Rules:
        - Technical terms (e.g., "TiDB", "TiKV") should never be classified as personas
        - Terms containing "system", "service", "tool", "platform" should be classified as features
        - Terms containing "Department", "Team", "Manager", "Director" should be classified as personas
        - Terms containing "case", "customer success", "implementation scenario" should be classified as cases
        - Case entities must contain measurable results and implementation details
        - Generic terms without clear classification should be excluded
        - Only extract entities that are explicitly mentioned in the text
        - Do not infer or make assumptions about missing information
        - Each entity must have clear evidence in the source text
            
    2. Establish Relationships:
      Valid Relationship Patterns:
        1. Persona experiences Pain Point
        2. Pain Point is addressed by Feature
        3. Feature is demonstrated by Case
      
      Required Elements for Each Relationship Type:
      A. "Persona experiences Pain Point":
        Must include these core elements in description:
        - Problem identification
        - Impact on business operations (with metrics if possible)
        - Frequency or pattern of occurrence
        Example: "Enterprise IT Directors face system integration challenges weekly, resulting in 20% productivity loss."
      
      B. "Pain Point is addressed by Feature":
        Must include these core elements in description:
        - Solution mechanism
        - Effectiveness (with metrics if possible)
        - Time to value
        Example: "The integration challenges are resolved through automated integration, reducing integration time by 90% with immediate productivity gains after 2-day setup."

      C. "Feature is demonstrated by Case":
        Must include these core elements in description:
        - Industry and business scenario (domain)
        - Quantifiable implementation results (outcomes)
        - At least 1 related feature/product (features)
        Example: "HTAP capability is demonstrated in a financial risk control case, reducing query latency by 80% for Bank X"
    

      Critical Rules for Relationships:
        - Must follow exact sequence: Persona -> Pain Point -> Feature -> Case
        - Each relationship must be part of a complete chain
        - No direct Persona-to-Feature relationships
        - No direct Persona-to-Case relationships
        - No direct Pain Point-to-Case relationships
        - No reverse relationships
        - No relationships between same entity types
        - Both source and target entities must exist and be valid
        - Must use exact entity names in relationships
        - Only extract relationships that are explicitly mentioned in the text
        - Do not infer or make assumptions about relationships
          
    3. Quality Guidelines:
      Basic Entity Information:
        - Each entity MUST have:
          * name: Clear, specific identifier (must be from source text)
          * description: Detailed description in complete sentences (must be based on source text)
          * metadata.topic: Must be exactly one of: "persona", "pain_point", "feature", or "case"
       
      Relationship Rules:
        - Follow strict sequence: Persona -> Pain Point -> Feature -> Case
        - Each relationship must form part of a complete chain
        - Never create direct Persona-to-Feature relationships
        - Never create direct Persona-to-Case relationships
        - Never create direct Pain Point-to-Case relationships
        - Never create reverse relationships
        - Never skip steps in the sequence
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
                    "topic": "persona|pain_point|feature"
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

    1. Persona entities:
        {
            "topic": "persona",  # Must be first field and keep unchanged from input
            "industry": "specific industry name",  # Optional
            "persona_type": "organization or department type",  # Optional
            "role": {  # Optional object
                "title": "specific job title",  # Optional
                "level": "c_level|middle_management|operational_staff"  # Optional
            }
        }

    2. Pain Point entities:
        {
            "topic": "pain_point",  # Must be first field and keep unchanged from input
            "scenario": "specific context",  # Optional
            "impact": "quantifiable business impact",  # Optional
            "severity": "Critical|High|Medium|Low"  # Optional
        }
        
    3. Feature entities:
        {
            "topic": "feature",  # Must be first field and keep unchanged from input
            "benefits": ["specific business benefit 1", "benefit 2"],  # Optional, must be array if present
            "technical_details": {  # Optional
                "key1": "value1",
                "key2": "value2"
            }
        }
    4. Case entities:
        {
            "topic": "case",  # Must be first field and keep unchanged from input
            "domain": "specific industry name",  # Optional
            "features": ["product/feature name 1", "product/feature name 2"],  # Optional, array format if present
            "outcomes": "quantifiable implementation results",  # Optional
            "references": "reference customer/implementation period information",  # Optional
        }

    Requirements:
    1. Field Requirements:
       - Topic field must be first and keep the value from input entity unchanged
       - Only extract fields that are explicitly mentioned in the source text
       - Do not infer or make assumptions about missing information
       - Empty or null values are not allowed for any field
    
    2. Data Quality:
       - All values must be specific and verifiable in the source text
       - Use consistent terminology across all metadata
       - Make values quantifiable where possible
       - Avoid generic or vague descriptions
    
    3. Format Rules:
       - String values must be properly formatted and meaningful
       - Arrays must be properly formatted as lists
       - Objects must be properly formatted as key-value pairs
       - Field names must exactly match the schema definition
       - Maintain consistent data types as specified
    
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