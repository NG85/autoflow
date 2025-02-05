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
        * Personas (who): Organizations or departments that are potential customers, which may include specific roles within those organizations or departments
          Examples:
          - "Enterprise IT Department in Healthcare"
          - "Bank's Security Operations Team"
          - "Manufacturing Company's R&D Division"
          - "Marketing Manager in Financial Services"
        * Pain Points (what): challenges, problems, needs
        * Features (how): solutions, capabilities, functionalities
      - Important Guidelines: 
        - Exclude technical terms or product names (e.g., "TiDB", "TiKV", "TiCDC") from being classified as personas.
        - Classify as a feature if the entity name contains keywords like "system", "service", "tool", or "platform".
        - Classify as a persona if the entity name contains keywords like "Department", "Team", "Manager", or "Director".
        
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
      - ONLY establish relationships in these two patterns: 
        1. Persona experiences Pain Point
        2. Pain Point is addressed/solved/resolved by Feature
      
      For each relationship, provide:

      A. "Persona experiences Pain Point":
        - Must include:
          * Which specific pain point this persona faces
          * Impact severity and frequency
          * Business consequences
          * Priority level
        Example: "Enterprise IT Directors frequently encounter system integration challenges, causing severe operational disruptions weekly, resulting in 20% productivity loss, making this a top priority issue."
      
      B. "Pain Point is addressed by Feature":
        - Must include:
          * How this feature addresses/solves the pain point
          * Solution effectiveness
          * Implementation requirements
          * Time to value
          * Success metrics
        Example: "The integration challenges are addressed by the automated integration system, achieving 90% reduction in integration time, requiring only 2-3 days setup, and delivering immediate productivity gains."

      CRITICAL RULES:
      * DO: 
        - Create relationships in this sequence ONLY:
          Persona -> experiences -> Pain Point
          Pain Point -> is addressed by -> Feature
        - Ensure each relationship forms part of a complete chain
        
       * DO NOT:
        - Create direct Persona-to-Feature relationships
        - Create reverse relationships (e.g. Feature solves Pain Point)
        - Skip any steps in the sequence
        - Create any other types of relationships
        - Create isolated relationships that don't connect to a complete chain
  
    3. Quality Guidelines:
      - Do not mark as a valid entity if classified as `persona` but lacking required metadata (e.g., `industry`, `persona_type`).
      - An entity must have all required metadata fields to be considered valid; if only the `topic` is identified, it is not valid.
      - An entity must have at least one associated relationship to be considered valid; if isolated, it is not valid.
      - Extract all meaningful entities and relationships from the text in one pass
      - Ensure all extracted information is factual and verifiable within the text
      - Use consistent terminology across entities and relationships
      - Make descriptions specific, quantifiable, and actionable where possible
      - For Persona entities:
        * If an entity is classified as `persona` but lacks other required metadata (e.g., `industry`, `persona_type`), do not mark it as a valid entity.
        * Use clear, descriptive names that combine industry and type
        * Use standard industry terms and role titles
        * Properly classify roles into management levels when available
        * Include only verifiable metadata from the text
      - For Relationships:
        * Strictly follow the sequence: Persona experiences Pain Point, Pain Point is addressed by Feature
        * Never create cross-level relationships (e.g., Persona -> Feature)
        * Never create reverse relationships (e.g., Feature -> Painpoint)
        * Never skip steps in the sequence
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