import logging
from typing import List, Optional
import dspy
from dspy.functional import TypedPredictor

from app.rag.indices.knowledge_graph.extractor import SimpleGraphExtractor
from app.rag.indices.knowledge_graph.schema import EntityCovariateInput, EntityCovariateOutput, KnowledgeGraph

logger = logging.getLogger(__name__)

class ExtractPlaybookTriplet(dspy.Signature):
    """Carefully analyze the provided text to identify sales and marketing related entities and their relationships.
    
    Follow these Step-by-Step Analysis:

    1. Extract Key Entities:
      For each entity type below, carefully validate ALL required fields before including:

      PERSONA entities (specific roles within organizations):
      - Required fields:
        * topic: Must be "persona"
        * industry: Specific industry sector name (e.g., "Financial Services", "E-commerce")
        * persona_type: Type of organization or department (e.g., "Enterprise Company", "IT Department", "Security Team")
        * role: Job position or title
        * role_level: Must be one of ['c_level', 'middle_management', 'operational_staff']
      - Role Classification Rules:
        * c_level: Executive positions (CTO, CIO, CEO, CFO, etc.)
        * middle_management: Team leads and managers (IT Manager, Engineering Director, etc.)
        * operational_staff: Hands-on practitioners (DBA, System Engineer, etc.)
      - Incomplete Information Handling:
        When text only mentions industry and persona_type without specific role:
        * role: Use "Unspecified Role"
        * role_level: Use "middle_management"
      - EXCLUDE if:
        * Missing any required field
        * Context is too vague or generic
        * Is a technical product name (e.g., TiDB, MySQL)
        * Is a generic term without organizational context (e.g., "users", "customers")

      PAIN_POINT entities (business challenges):
      - Required fields:
        * topic: Must be "pain_point"
        * scenario: Specific business context where pain occurs
        * impact: Concrete business consequences or losses
      - Optional fields:
        * severity: Level of severity (e.g., "Critical", "High", "Medium", "Low")
      - EXCLUDE if:
        * Missing any required field (topic, scenario, impact)
        * Only describes technical issues without business impact
        * Too generic without specific context
        * Impact is not quantifiable or clearly defined
      - Severity Guidelines (when provided):
        * Critical: Immediate business operation impact
        * High: Significant business performance impact
        * Medium: Moderate efficiency or cost impact
        * Low: Minor inconvenience or potential risk

      FEATURE entities (solution capabilities):
      - Required fields:
        * topic: Must be "feature"
        * benefits: Array of specific business benefits, each benefit must be:
          - Concrete and measurable
          - Focused on business value
          - Clearly stated in complete sentences
      - Optional fields:
        * technical_details: Key-value pairs of technical specifications, where:
          - Key: Technical aspect name
          - Value: Detailed specification or requirement
      - EXCLUDE if:
        * Missing benefits array or benefits array is empty
        * Benefits are too vague or unmeasurable
        * Only contains technical specifications without business benefits
        * Benefits cannot be verified from the text

    2. Identify Relationships:
      ONLY establish relationships in these two patterns AND DIRECTIONS:
      
      A. "Persona experiences Pain Point" (DIRECTION: Persona -> Pain Point):
      - Required elements:
        * Source: MUST be Persona entity ONLY
        * Target: MUST be Pain Point entity ONLY
        * Description must include:
          - Clear connection between the persona and pain point
          - How frequently or severely the persona experiences this pain
          - Business impact on the persona
      - Example: "Enterprise IT Directors frequently face data integration challenges, causing 30% productivity loss"
      - INVALID: Pain Point -> Persona direction
      
      B. "Pain Point is addressed by Feature" (DIRECTION: Pain Point -> Feature):
      - Required elements:
        * Source: MUST be Pain Point entity ONLY
        * Target: MUST be Feature entity ONLY
        * Description must include:
          - How the feature specifically solves the pain point
          - Quantifiable benefits or improvements
          - Implementation or adoption context
      - Example: "The automated integration feature resolves these challenges by reducing integration time by 90%"
      - INVALID: Feature -> Pain Point direction

      Strict Relationship Rules:
      - ONLY VALID DIRECTIONS:
        * Persona -> Pain Point
        * Pain Point -> Feature
      - INVALID DIRECTIONS (MUST BE EXCLUDED):
        * Pain Point -> Persona
        * Feature -> Pain Point
        * Persona -> Feature
        * Feature -> Persona
      - Each relationship MUST be explicitly supported by text evidence
      - Each relationship MUST include all required descriptive elements
      - NO inferred relationships without clear text evidence

    3. Quality Guidelines:
      - STRICT VALIDATION RULES for Entities:
        * Each entity MUST have ALL required metadata fields for its type
        * Each entity MUST be part of at least one complete relationship chain
        * Each entity MUST have specific, verifiable information from the text
        * Technical product names (e.g., TiDB, TiKV) are NOT valid entities
        * Generic terms without specific metadata are NOT valid entities
   
      - For Persona entities:
        * MUST have both industry AND persona_type fields
        * Industry field MUST be a recognized sector name
        * Persona_type MUST be either organization type or department type
        * Role MUST be either a specific job title or "Unspecified Role"
        * Role_level MUST match the role when specified
        * When role is "Unspecified Role", role_level defaults to "middle_management"
     
      - For Pain Point entities:
        * MUST have both scenario AND impact fields
        * Scenario MUST describe specific business context
        * Impact MUST describe quantifiable business effect
        * Severity field is optional but must use standard levels if included
      
      - For Feature entities:
        * MUST have benefits array with at least one specific benefit
        * Each benefit MUST be:
          - Quantifiable or clearly defined
          - Business-focused
          - Supported by text evidence
        * Technical_details (if included) MUST be:
          - Structured as key-value pairs
          - Specifically mentioned in text
          - Relevant to the feature's capabilities
        
      - For Relationships:
        * STRICT DIRECTIONAL RULES:
          - ONLY these directions are valid:
            > Persona -> Pain Point
            > Pain Point -> Feature
          - These directions are INVALID and MUST be excluded:
            > Pain Point -> Persona
            > Feature -> Pain Point
            > Persona -> Feature
            > Feature -> Persona
        
        * For "Persona experiences Pain Point" (Persona -> Pain Point):
          - Source MUST be Persona entity ONLY
          - Target MUST be Pain Point entity ONLY
          - MUST show clear connection between persona and pain point
          - MUST include frequency or severity of the pain
          - MUST describe business impact on the persona

        * For "Pain Point is addressed by Feature" (Pain Point -> Feature):
          - Source MUST be Pain Point entity ONLY
          - Target MUST be Feature entity ONLY
          - MUST explain how feature solves the pain point
          - MUST include quantifiable benefits
          - MUST provide implementation context

        * General Relationship Rules:
          - Each relationship MUST have clear text evidence
          - NO relationships between non-adjacent entities
          - NO reverse relationships
          - NO inferred relationships without evidence
          - Each relationship MUST include all required descriptive elements

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
    4. Ensure all metadata values are specific and verifiable in the text
    
    Required metadata structure by entity type:

    1. Persona entities:
        {
            "topic": "persona",  # Must be first field
            "industry": "specific industry sector name",  # Required
            "persona_type": "organization or department type",  # Required
            "role": "specific job title or 'Unspecified Role'",  # Required
            "role_level": "c_level|middle_management|operational_staff"  # Required
        }
        - Role Level Classification:
          * c_level: Executive positions (CTO, CIO, CEO, etc.)
          * middle_management: Team leads and managers
          * operational_staff: Hands-on practitioners
        - Default Values:
          * When only industry and persona_type are present:
            - role: "Unspecified Role"
            - role_level: "middle_management"

    2. Pain Point entities:
        {
            "topic": "pain_point",  # Must be first field
            "scenario": "specific business context",  # Required
            "impact": "quantifiable business impact",  # Required
            "severity": "Critical|High|Medium|Low"  # Optional
        }
        - Severity Guidelines:
          * Critical: Immediate business operation impact
          * High: Significant business performance impact
          * Medium: Moderate efficiency or cost impact
          * Low: Minor inconvenience or potential risk

    3. Feature entities:
        {
            "topic": "feature",  # Must be first field
            "benefits": [  # Required, array of strings
                "specific business benefit 1",
                "specific business benefit 2"
            ],
            "technical_details": {  # Optional, key-value pairs
                "aspect1": "specification1",
                "aspect2": "specification2"
            }
        }
        - Benefits Requirements:
          * Each benefit must be concrete and measurable
          * Must focus on business value
          * Must be clearly stated in complete sentences
        - Technical Details Format:
          * Key: Technical aspect name
          * Value: Detailed specification or requirement

    Validation Rules:
    - Each covariate must include all required fields for its type
    - All values must be specific and verifiable in the source text
    - Generic or vague descriptions are not acceptable
    - Technical terms without business context are not acceptable
    - Optional fields should only be included if explicitly supported by the text

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