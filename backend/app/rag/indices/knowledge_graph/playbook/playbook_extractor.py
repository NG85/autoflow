import logging
from typing import List, Optional
import dspy
from dspy.functional import TypedPredictor

from app.rag.indices.knowledge_graph.extractor import SimpleGraphExtractor
from app.rag.indices.knowledge_graph.schema import EntityCovariateInput, EntityCovariateOutput, KnowledgeGraph

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

      Important Classification Rules:
        - Technical terms (e.g., "TiDB", "TiKV") should never be classified as personas
        - Terms containing "system", "service", "tool", "platform" should be classified as features
        - Terms containing "Department", "Team", "Manager", "Director" should be classified as personas
        - Generic terms without clear classification should be excluded
            
    2. Establish Relationships:
      Valid Relationship Patterns:
        1. Persona experiences Pain Point
        2. Pain Point is addressed by Feature
      
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
    """Please carefully review the provided text and entities list. Extract detailed metadata for each entity based on its type.
    
    Required metadata structure by entity type:

    1. Persona entities:
        {
            "topic": "persona",  # Must be first field and keep unchanged from input
            "industry": "specific industry name",  # Required
            "persona_type": "organization or department type",  # Required
            "role": {  # Optional object
                "title": "specific job title",  # Required if role is present
                "level": "c_level|middle_management|operational_staff"  # Required if role is present
            }
        }

    2. Pain Point entities:
        {
            "topic": "pain_point",  # Must be first field and keep unchanged from input
            "scenario": "specific context",  # Required
            "impact": "quantifiable business impact",  # Required
            "severity": "Critical|High|Medium|Low"  # Optional
        }
        
    3. Feature entities:
        {
            "topic": "feature",  # Must be first field and keep unchanged from input
            "benefits": ["specific business benefit 1", "benefit 2"],  # Required, must be array
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
      super().__init__(dspy_lm, complied_extract_program_path)
      # Replace the default extractor with Playbook specific extractor
      self.extract_prog.prog_graph = TypedPredictor(ExtractPlaybookTriplet)
      self.extract_prog.prog_covariates = TypedPredictor(ExtractPlaybookCovariate)