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
      
      For each entity type, extract required metadata:
      
      Persona Entities (Organizations and their key roles):
        Required metadata:
          - topic: Must be "persona"
          - industry: Target industry sector
            Example: "Healthcare", "Financial Services"
          - persona_type: Type of organization or department
            Example: "Enterprise Company", "IT Department"
        Optional metadata:
          - role: Object containing role information (omit if not clear from text)
            If present, must include both:
            - title: Job position or title
            - level: Must be exactly one of: ["c_level", "middle_management", "operational_staff"]
      
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
          - benefits: Array of specific business benefits
            Example: ["Reduces integration time by 70%"]
        Optional metadata:
          - technical_details: Technical specifications as key-value pairs
            Example: {"api_support": "REST API"}
            
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
      Entity Validation Rules:
        - Every entity MUST have a valid "topic" field with exact values: "persona", "pain_point", or "feature"
        - Each entity type must include ALL required metadata fields:
          * Persona entities:
            - Required: topic, industry, persona_type
            - Optional: role (if present, must contain both title and level)
          * Pain Point entities:
            - Required: topic, scenario, impact
            - Optional: severity
          * Feature entities:
            - Required: topic, benefits (as array of strings)
            - Optional: technical_details (as key-value pairs)
        
      Data Quality Rules:
        - All metadata values must be factual and verifiable within the source text
        - Use consistent terminology across entities and relationships
        - Make descriptions specific and quantifiable where possible
        - An entity without all required metadata fields is invalid and should be excluded
        - An entity must have at least one associated relationship to be considered valid
        
      Relationship Rules:
        - Follow strict sequence: Persona -> Pain Point -> Feature
        - Each relationship must form part of a complete chain
        - Never create direct Persona-to-Feature relationships
        - Never create reverse relationships
        - Never skip steps in the sequence
        - Both source and target entities must be valid
        - Relationship descriptions must be specific and verifiable
        - Must use exact entity names in relationships
        
      Format Requirements:
        - All string values should be properly formatted and meaningful
        - Arrays (like benefits) must be properly formatted as lists
        - Objects (like technical_details) must be properly formatted as key-value pairs
        - Maintain consistent data types as specified in entity definitions:
          * String fields: industry, persona_type, scenario, impact, severity
          * Array fields: benefits
          * Object fields: technical_details, role (if present)
          * Enum fields: role.level must be one of ["c_level", "middle_management", "operational_staff"]
        - Empty or null values are not allowed for required fields
        - All field names must exactly match the schema definition

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
    
    Required metadata structure by entity type:

    1. Persona entities:
        {
            "topic": "persona",  # Must be first field
            "industry": "specific industry name",  # Required
            "persona_type": "organization or department type",  # Required
            "role": {  # Optional object
                "title": "specific job title",  # Required if role is present
                "level": "c_level|middle_management|operational_staff"  # Required if role is present
            }
        }

    2. Pain Point entities:
        {
            "topic": "pain_point",  # Must be exact value
            "scenario": "specific context",  # Required
            "impact": "quantifiable business impact",  # Required
            "severity": "Critical|High|Medium|Low"  # Optional
        }
        
    3. Feature entities:
        {
            "topic": "feature",  # Must be exact value
            "benefits": ["specific business benefit 1", "benefit 2"],  # Required, must be array
            "technical_details": {  # Optional
                "key1": "value1",
                "key2": "value2"
            }
        }

    Requirements:
    1. Field Requirements:
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