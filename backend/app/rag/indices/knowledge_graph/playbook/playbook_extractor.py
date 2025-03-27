import logging
from typing import List, Optional, Tuple
import dspy
from dspy.functional import TypedPredictor
import pandas as pd
from llama_index.core.schema import BaseNode

from app.rag.indices.knowledge_graph.extractor import SimpleGraphExtractor, get_relation_metadata_from_node
from app.rag.indices.knowledge_graph.schema import EntityCovariateInput, EntityCovariateOutput, KnowledgeGraph
from app.models.enums import GraphType
from app.rag.indices.knowledge_graph.extract_template import (
    PLAYBOOK_EXTRACTION_TEMPLATE,
    PLAYBOOK_COVARIATE_TEMPLATE,
)

logger = logging.getLogger(__name__)

class ExtractPlaybookTriplet(dspy.Signature):
    """Carefully analyze the provided text to identify sales related entities and their relationships for Zai Lab's pharmaceutical products.
    
    Follow these Step-by-Step Analysis:

    1. Extract Key Entities:
      First, identify significant entities from the text:
        * Personas (who): Organizations or healthcare professionals that are potential customers/partners
          Examples:
          - "Oncologists in Tertiary Hospitals"
          - "Immunology Department in Research Institutions"
          - "Hospital Pharmacy Directors"
          - "Regulatory Affairs Specialists"
        * Pain Points (what): Treatment challenges, unmet medical needs, healthcare delivery problems
          Examples:
          - "Limited Treatment Options for Advanced NSCLC"
          - "High Toxicity of Current Therapies"
          - "Drug Resistance in Cancer Patients"
          - "Prolonged Regulatory Approval Process"
        * Features (how): Zai Lab's pharmaceutical solutions, technologies, and capabilities
          Examples:
          - "Targeted Therapy with Reduced Side Effects"
          - "Companion Diagnostic Technology"
          - "Accelerated Clinical Development Platform"
          - "Novel Mechanism of Action in Immunotherapy"

      Important Classification Rules:
        - Technical terms (e.g., "protein", "biomarker") should never be classified as personas
        - Terms containing "therapy", "treatment", "platform", "technology" should be classified as features
        - Terms containing "Specialist", "Physician", "Director", "Department" should be classified as personas
        - Generic terms without clear classification should be excluded
            
    2. Establish Relationships:
      Valid Relationship Patterns:
        1. Persona experiences Pain Point
        2. Pain Point is addressed by Feature
      
      Required Elements for Each Relationship Type:
      A. "Persona experiences Pain Point":
        Must include these core elements in description:
        - Problem identification
        - Impact on patient outcomes or healthcare delivery (with metrics if possible)
        - Frequency or pattern of occurrence
        Example: "Oncologists in Specialized Cancer Centers face treatment resistance in 40% of patients with advanced solid tumors, leading to disease progression within 6-12 months of initial response."
      
      B. "Pain Point is addressed by Feature":
        Must include these core elements in description:
        - Solution mechanism
        - Effectiveness (with clinical data if possible)
        - Time to clinical benefit
        Example: "The treatment resistance challenge is addressed through Dual-Targeting Immunotherapy, showing 65% reduction in resistance development in phase II clinical trials with benefits observed within 8 weeks of treatment initiation."

      Critical Rules for Relationships:
        - Must follow exact sequence: Persona -> Pain Point -> Feature
        - Each relationship must be part of a complete chain
        - No direct Persona-to-Feature relationships
        - No reverse relationships
        - No relationships between same entity types
        - Both source and target entities must exist and be valid
        - Must use exact entity names in relationships
          
    3. Quality Guidelines:
      Basic Entity Information:
        - Each entity MUST have:
          * name: Clear, specific identifier
          * description: Detailed description in complete sentences
          * metadata.topic: Must be exactly one of: "persona", "pain_point", or "feature"
       
      Relationship Rules:
        - Follow strict sequence: Persona -> Pain Point -> Feature
        - Each relationship must form part of a complete chain
        - Never create direct Persona-to-Feature relationships
        - Never create reverse relationships
        - Never skip steps in the sequence
        - Both source and target entities must be valid
        - Relationship descriptions must be specific and verifiable
        - Must use exact entity names in relationships

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
    """Please carefully review the provided text and entities list. Extract detailed metadata for each entity based on its type within the pharmaceutical and healthcare context.
    
    Required metadata structure by entity type:

    1. Persona entities:
        {
            "topic": "persona",  # Must be first field and keep unchanged from input
            "industry": "specific healthcare sector",  # Required (e.g., "oncology", "immunology", "hospital administration")
            "persona_type": "healthcare professional or institution type",  # Required
            "role": {  # Optional object
                "title": "specific medical or administrative title",  # Required if role is present
                "level": "c_level|specialist|researcher|general_practitioner"  # Required if role is present
            }
        }

    2. Pain Point entities:
        {
            "topic": "pain_point",  # Must be first field and keep unchanged from input
            "scenario": "specific clinical or healthcare context",  # Required
            "impact": "quantifiable patient outcome or healthcare delivery impact",  # Required
            "severity": "Critical|High|Medium|Low"  # Optional
        }
        
    3. Feature entities:
        {
            "topic": "feature",  # Must be first field and keep unchanged from input
            "benefits": ["specific clinical benefit 1", "benefit 2"],  # Required, must be array
            "technical_details": {  # Optional
                "key1": "value1",
                "key2": "value2"
            }
        }

    Requirements:
    1. Field Requirements:
       - topic field must be first and keep the value from input entity unchanged
       - Each entity must have all required fields for its type
       - Optional fields should only be included if clear information is present in the text
       - The 'role' object for Personas must include both title and level if present
       - Empty or null values are not allowed for required fields
    
    2. Data Quality:
       - All values must be specific and verifiable in the source text
       - Use consistent terminology across all metadata
       - Make values quantifiable where possible (e.g., efficacy rates, survival improvements)
       - Avoid generic or vague descriptions
       - For pharmaceutical context, focus on clinical outcomes, patient benefits, and therapeutic advantages
    
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
      with dspy.settings.context(instructions=PLAYBOOK_EXTRACTION_TEMPLATE):
          self.extract_prog.prog_graph = TypedPredictor(ExtractPlaybookTriplet)

      with dspy.settings.context(instructions=PLAYBOOK_COVARIATE_TEMPLATE):
          self.extract_prog.prog_covariates = TypedPredictor(ExtractPlaybookCovariate)

          
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