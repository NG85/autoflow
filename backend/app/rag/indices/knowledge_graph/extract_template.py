EXTRACTION_TEMPLATE = """
Carefully analyze the provided text from FinClip documentation and technical resources to thoroughly identify all entities related to mini-program container technology, including both general concepts and specific details.

Follow these Step-by-Step Analysis:

1. Extract Meaningful Entities:
- Identify all significant nouns, proper nouns, and technical terminologies that represent mini-program related concepts, components, features, APIs, implementation steps, use cases, platforms, versions, or any substantial entities.
- Ensure that you capture entities across different levels of detail, from high-level overviews to specific technical specifications, to create a comprehensive representation of FinClip's mini-program container technology.
- Choose names for entities that are specific enough to indicate their meaning without additional context, avoiding overly generic terms.
- Consolidate similar entities to avoid redundancy, ensuring each represents a distinct concept at appropriate granularity levels.

2. Extract Metadata to claim the entities:
- Carefully review the provided text, focusing on identifying detailed covariates associated with each entity.
- Extract and link the covariates (which is a comprehensive json TREE, the first field is always: "topic") to their respective entities.
- Ensure all extracted covariates is clearly connected to the correct entity for accuracy and comprehensive understanding.
- Ensure that all extracted covariates are factual and verifiable within the text itself, without relying on external knowledge or assumptions.
- Collectively, the covariates should provide a thorough and precise summary of the entity's characteristics as described in the source material.

3. Establish Relationships:
- Carefully examine the text to identify all relationships between clearly-related entities, ensuring each relationship is correctly captured with accurate details about the interactions.
- Analyze the context and interactions between the identified entities to determine how they are interconnected, focusing on actions, associations, dependencies, or similarities.
- Clearly define the relationships, ensuring accurate directionality that reflects the logical or functional dependencies among entities. \
   This means identifying which entity is the source, which is the target, and what the nature of their relationship is (e.g., $source_entity depends on $target_entity for $relationship).

Some key points to consider:
- Focus on FinClip-specific terminology, including container technology, mini-program lifecycle, cross-platform capabilities, API interfaces, and integration methods.
- Pay special attention to technical implementation details, deployment processes, and compatibility considerations.
- Please endeavor to extract all meaningful entities and relationships from the text, avoid subsequent additional gleanings.

Objective: Produce a detailed and comprehensive knowledge graph that captures the full spectrum of entities mentioned in the text, along with their interrelations, reflecting both broad concepts and intricate details specific to FinClip's mini-program container technology.

Please only response in JSON format.
"""
   

COVARIATE_TEMPLATE = """
Please carefully review the provided text and entities list which are already identified in the text. Focusing on identifying detailed covariates associated with each entities provided.
Extract and link the covariates (which is a comprehensive json TREE, the first field is always: "topic") to their respective entities.
Ensure all extracted covariates is clearly connected to the correct entity for accuracy and comprehensive understanding.
Ensure that all extracted covariates are factual and verifiable within the text itself, without relying on external knowledge or assumptions.
Collectively, the covariates should provide a thorough and precise summary of the entity's characteristics as described in the source material.

For FinClip-specific entities, pay special attention to technical specifications, implementation requirements, compatibility information, and usage scenarios related to mini-program container technology.

Please only response in JSON format.
"""


PLAYBOOK_EXTRACTION_TEMPLATE = """
Carefully analyze the provided text to identify FinClip sales related entities and their relationships.

Follow these Step-by-Step Analysis:

1. Extract Key Entities:
First, identify significant entities from the text:
   * Personas (who): Organizations or departments that are potential customers for FinClip
      Examples:
      - "Financial Institution's Mobile Banking Team"
      - "Retail Company's Digital Experience Department"
      - "Insurance Company's App Development Team"
      - "E-commerce Platform's Technical Director"
   * Pain Points (what): Business challenges, problems, needs related to mobile applications
   * Features (how): FinClip solutions, capabilities, functionalities

Important Classification Rules:
   - Technical terms (e.g., "FinClip", "Mini-program container") should never be classified as personas
   - Terms containing "system", "service", "tool", "platform", "container", "SDK", "API" should be classified as features
   - Terms containing "Department", "Team", "Manager", "Director", "Developer" should be classified as personas
   - Generic terms without clear classification should be excluded
      
2. Establish Relationships:
Valid Relationship Patterns:
   1. Persona experiences Pain Point
   2. Pain Point is addressed by Feature

Required Elements for Each Relationship Type:
A. "Persona experiences Pain Point":
   Must include these core elements in description:
   - Problem identification in mobile app development or deployment
   - Impact on business operations (with metrics if possible)
   - Frequency or pattern of occurrence
   Example: "E-commerce App Development Teams face content update challenges weekly, resulting in 30% delay in feature releases and market opportunities."

B. "Pain Point is addressed by Feature":
   Must include these core elements in description:
   - Solution mechanism using FinClip technology
   - Effectiveness (with metrics if possible)
   - Time to value
   Example: "The content update challenges are resolved through FinClip's dynamic mini-program deployment, reducing update time by 95% with immediate content delivery without app store approval process."

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


PLAYBOOK_COVARIATE_TEMPLATE = """
Please carefully review the provided text and entities list. Extract detailed metadata for each entity based on its type.

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
      "scenario": "specific mobile app development or deployment context",  # Required
      "impact": "quantifiable business impact",  # Required
      "severity": "Critical|High|Medium|Low"  # Optional
   }
   
3. Feature entities:
   {
      "topic": "feature",  # Must be first field and keep unchanged from input
      "benefits": ["specific business benefit 1", "benefit 2"],  # Required, must be array
      "technical_details": {  # Optional
            "platform_compatibility": "iOS|Android|both",  # FinClip specific
            "integration_complexity": "simple|moderate|complex",  # FinClip specific
            "update_mechanism": "specific update process"  # FinClip specific
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
   - For FinClip-specific fields, ensure values accurately reflect mini-program container technology characteristics

3. Format Rules:
   - String values must be properly formatted and meaningful
   - Arrays must be properly formatted as lists
   - Objects must be properly formatted as key-value pairs
   - Field names must exactly match the schema definition
   - Maintain consistent data types as specified

Please only response in JSON format.
"""