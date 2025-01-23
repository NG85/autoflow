import logging
from typing import List, Optional
import dspy
from dspy.functional import TypedPredictor

from app.rag.knowledge_graph.extractor import SimpleGraphExtractor
from app.rag.knowledge_graph.schema import EntityCovariateInput, EntityCovariateOutput, KnowledgeGraph

logger = logging.getLogger(__name__)

class ExtractPlaybookTriplet(dspy.Signature):
    """Carefully analyze the provided text to identify sales and marketing related entities and their relationships.
    
    Follow these Step-by-Step Analysis:

    1. Extract Key Entities:
      - First, identify all significant nouns, proper nouns, and technical terms that represent:
         * Personas (who): Organizations or departments that are potential customers
          Examples:
          - "Enterprise IT Department in Healthcare"
          - "Bank's Security Operations Team"
          - "Manufacturing Company's R&D Division"
        * Pain Points (what): challenges, problems, needs
        * Features (how): solutions, capabilities, functionalities
      
      For each entity type, extract required metadata:
      
      Persona Entities (Organizations and their key roles):
        Required metadata:
          - topic: Must be "persona"
          - industry: Target industry sector
            Examples: "Healthcare", "Financial Services", "Manufacturing"
          - persona_type: Type of organization or department
            Examples:
            - Organization types: "Enterprise Company", "Tech Startup"
            - Department types: "IT Department", "Security Team"
        Optional metadata:
          - roles: List of key decision makers and stakeholders
            Format: [
              {
                "title": "Role description",
                "level": "c_level|middle_management|operational_staff"
              }
            ]
            Examples:
            - IT Department roles:
              * {"title": "Chief Technology Officer", "level": "c_level"}
              * {"title": "IT Infrastructure Manager", "level": "middle_management"}
              * {"title": "System Administrator", "level": "operational_staff"}
      
      Pain Point Entities (What problems need solving):
        Required metadata:
          - topic: Must be "pain_point"
          - scenario: Specific context where the problem occurs
            Example: "During system integration"
          - impact: Business or operational impact
            Example: "20% decrease in productivity"
        Optional metadata:
          - severity: Impact level on business operations
            Example: "Critical", "High", "Medium", "Low"
      
      Feature Entities (What solutions we offer):
        Required metadata:
          - topic: Must be "feature"
          - benefits: List of specific business benefits
            Example: ["Reduces integration time by 70%"]
        Optional metadata:
          - technical_details: Technical specifications
          Example: "REST API support", "256-bit encryption"
  
    2. Establish Relationships:
      - Carefully examine how entities interact with each other
      - Focus on clear and direct relationships that are explicitly stated in the text
      - Ensure relationship descriptions are comprehensive and include all required elements
      - Follow strict relationship hierarchy: Persona -> Pain Point -> Feature
      - DO NOT create any cross-level or reverse relationships
      
      Valid relationship patterns and required description elements:
      
      1. Persona -> Pain Point (ONLY this direction):
        Description must include:
        * How severely this pain point affects the persona
        * How frequently they encounter this issue
        * What business impact it has on their operations
        * Their priority level for addressing this pain point
        Example: "Enterprise IT Directors frequently encounter system integration challenges, causing severe operational disruptions weekly, resulting in 20% productivity loss, making this a top priority issue."
      
      2. Pain Point -> Feature (ONLY this direction):
        Description must include:
        * How effectively the feature addresses the problem
        * What implementation effort is required
        * Expected time to realize benefits
        * Key success criteria
        Example: "The automated integration feature completely eliminates manual integration work, requires minimal setup effort of 2-3 days, delivers immediate productivity gains, with success measured by 90% reduction in integration time."

      INVALID relationships (DO NOT CREATE):
      - Feature -> Pain Point (wrong direction)
      - Pain Point -> Persona (wrong direction)
      - Persona -> Feature (cross-level)
      - Feature -> Persona (cross-level and wrong direction)
  
    3. Quality Guidelines:
      - Extract all meaningful entities and relationships from the text in one pass
      - Ensure all extracted information is factual and verifiable within the text
      - Use consistent terminology across entities and relationships
      - Make descriptions specific, quantifiable, and actionable where possible
      - For Persona entities:
        * Use clear, descriptive names that combine industry and type
        * Use standard industry terms and role titles
        * Properly classify roles into management levels when available
        * Include only verifiable metadata from the text
      - For Relationships:
        * Strictly follow the hierarchy: Persona -> Pain Point -> Feature
        * Never create cross-level relationships (e.g., Persona -> Feature)
        * Never create reverse relationships (e.g., Pain Point -> Persona)
        * Ensure each relationship description includes all required elements
        * Use quantifiable metrics and specific timeframes when available
        * Maintain consistent terminology with entity descriptions
  
    Please only response in JSON format:
    {
        "knowledge": {
            "entities": [
                {
                    "name": "entity name (specific and meaningful)",
                    "description": "detailed description in complete sentences",
                    "metadata": {
                        "topic": "persona|pain_point|feature",
                        ... // required and optional metadata fields
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
    }
    """

    text = dspy.InputField(desc="text to extract sales and marketing related entities and relationships")
    knowledge: KnowledgeGraph = dspy.OutputField(desc="Graph representation of the sales and marketing knowledge.")

class ExtractPlaybookCovariate(dspy.Signature):
    """Please carefully review the provided text and entities list. Extract detailed metadata for each entity based on its type.
    
    For each entity:
    1. Identify the entity type (persona, pain_point, or feature)
    2. Extract all required metadata fields for that type
    3. Add relevant optional metadata if clearly present in the text
    4. Ensure all metadata values are:
       - Specific and meaningful
       - Factual and verifiable in the text
       - Properly formatted according to the field type
    
    Required metadata structure by entity type:

    1. Persona entities:
        {
            "topic": "persona",  # Must be first field
            "industry": "specific industry name",  # Required
            "persona_type": "organization or department type",  # Required
            "roles": [  # Optional
                {
                    "title": "specific role title",
                    "level": "c_level|middle_management|operational_staff"
                }
            ]
        }

    2. Pain Point entities:
        {
            "topic": "pain_point",  # Must be first field
            "scenario": "specific context",  # Required
            "impact": "quantifiable business impact",  # Required
            "severity": "Critical|High|Medium|Low"  # Optional
        }

    3. Feature entities:
        {
            "topic": "feature",  # Must be first field
            "benefits": ["specific business benefit 1", "benefit 2"],  # Required
            "technical_details": "specific technical specifications"  # Optional
        }

    Requirements:
    - Each covariate must be a JSON tree structure
    - "topic" must always be the first field with exact values: "persona", "pain_point", or "feature"
    - All required fields must be included based on entity type
    - Optional fields should only be included if explicitly supported by the text
    - All values must be specific and verifiable in the source text
    - Each covariate must be correctly linked to its corresponding entity

    Please only response in JSON format.
    """
    
    text = dspy.InputField(desc="text to extract covariates for the entities")
    entities: List[EntityCovariateInput] = dspy.InputField(desc="List of entities identified in the text")
    covariates: List[EntityCovariateOutput] = dspy.OutputField(desc="Detailed metadata for each entity")


class PlaybookExtractor(SimpleGraphExtractor):
    """Extractor for sales playbook knowledge graph, using customized prompts for playbook entities and relationships"""
    def __init__(self, dspy_lm: dspy.LM, complied_extract_program_path: Optional[str] = None):
      super().__init__(dspy_lm, complied_extract_program_path)
      # Replace the default extractor with Playbook specific extractor
      self.extract_prog.prog_graph = TypedPredictor(ExtractPlaybookTriplet)
      self.extract_prog.prog_covariates = TypedPredictor(ExtractPlaybookCovariate)