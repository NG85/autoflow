EXTRACTION_TEMPLATE = """
Carefully analyze the provided text from Qingflow documentation and community blogs to thoroughly identify all entities related to no-code platform technologies, including both general concepts and specific details.

Follow these Step-by-Step Analysis:

1. Extract Meaningful Entities:
- Identify all significant nouns, proper nouns, and technical terminologies that represent no-code platform concepts, components, functional modules, form elements, workflow steps, execution order, user scenarios, interface elements, versions, or any substantial entities.
- Ensure that you capture entities across different levels of detail, from high-level overviews to specific technical specifications, to create a comprehensive representation of the subject matter.
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
- Please endeavor to extract all meaningful entities and relationships from the text, avoid subsequent additional gleanings.

Objective: Produce a detailed and comprehensive knowledge graph that captures the full spectrum of entities mentioned in the text, along with their interrelations, reflecting both broad concepts and intricate details specific to the no-code platform domain.

Please only respond in JSON format.
"""
   

COVARIATE_TEMPLATE = """
Please carefully review the provided text and entities list which are already identified in the text. Focus on identifying detailed covariates associated with each entity provided within the Qingflow no-code platform context.
    
For each no-code platform entity, extract and link comprehensive covariates to build a detailed JSON TREE (the first field is always: "topic"). Consider relevant attributes such as:
- Functionality and purpose within the no-code ecosystem
- Configuration options and customization capabilities
- Relationships with other platform components
- User interaction patterns and interface characteristics
- Technical limitations or requirements
- Implementation examples or use cases mentioned

Ensure all extracted covariates are clearly connected to the correct entity for accuracy and comprehensive understanding.
Ensure that all extracted covariates are factual and verifiable within the text itself, without relying on external knowledge or assumptions.
Collectively, the covariates should provide a thorough and precise summary of the entity's characteristics as described in the source material, focusing on aspects relevant to no-code platform development.

Please only respond in JSON format.
"""


PLAYBOOK_EXTRACTION_TEMPLATE = """
Carefully analyze the provided text to identify sales related entities and their relationships for Qingflow's no-code platform.

Follow these Step-by-Step Analysis:

1. Extract Key Entities:
First, identify significant entities from the text:
   * Personas (who): Organizations or departments that are potential customers of Qingflow
      Examples:
      - "Business Analysts in Financial Services" 
      - "HR Department in Healthcare"
      - "Operations Team in Manufacturing"
      - "IT Department with Limited Development Resources"
   * Pain Points (what): Business challenges, problems, needs that can be solved with no-code platforms
      Examples:
      - "Time-consuming Manual Workflow Approvals"
      - "Siloed Data Systems Across Departments"
      - "Slow Custom Application Development"
      - "Difficulty in Process Standardization"
   * Features (how): Qingflow platform capabilities and functionalities
      Examples:
      - "Visual Form Builder"
      - "Drag-and-Drop Workflow Designer"
      - "No-Code Database Integration"
      - "Automated Approval Process"

Important Classification Rules:
   - Technical terms (e.g., "API", "Database") should never be classified as personas
   - Terms containing "builder", "designer", "module", "component" should be classified as features
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
   Example: "HR Departments face time-consuming approval processes daily, resulting in 70% of staff time spent on administrative tasks rather than strategic initiatives."

B. "Pain Point is addressed by Feature":
   Must include these core elements in description:
   - Solution mechanism
   - Effectiveness (with metrics if possible)
   - Time to value
   Example: "The approval process bottlenecks are eliminated through Automated Approval Workflows, reducing process time by 85% and allowing implementation within 2 days without IT support."

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
   - For no-code platform context, focus on business impact and ease of implementation

3. Format Rules:
   - String values must be properly formatted and meaningful
   - Arrays must be properly formatted as lists
   - Objects must be properly formatted as key-value pairs
   - Field names must exactly match the schema definition
   - Maintain consistent data types as specified

Please only response in JSON format.
"""