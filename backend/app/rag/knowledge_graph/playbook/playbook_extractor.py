import logging
from typing import Mapping, Optional
import dspy
from dspy.functional import TypedPredictor
import pandas as pd

from app.rag.knowledge_graph.extractor import Extractor, get_relation_metadata_from_node
from app.rag.knowledge_graph.schema import Entity, KnowledgeGraph
from llama_index.core.schema import BaseNode

from app.rag.knowledge_graph.playbook.relationship import PlaybookRelationType, PlaybookRelationship

logger = logging.getLogger(__name__)

class ExtractPlaybookTriplet(dspy.Signature):
    """Carefully analyze the provided text to identify sales and marketing related entities and their relationships.
    
    Follow these Step-by-Step Analysis:

    1. Extract Key Entities:
      - Identify Personas (who are the target users/customers)
        Required metadata:
          - topic: "persona"
          - industry: target industry of the persona
          - role: (optional) specific role or position
      
      - Identify Pain Points (what problems they face)
        Required metadata:
          - topic: "pain_point"
          - scenario: specific context or situation
          - impact: business or operational impact
          - severity: (optional) level of severity
      
      - Identify Features (what solutions we offer)
        Required metadata:
          - topic: "feature"
          - benefits: [list of specific benefits]
          - technical_details: {key-value pairs of technical specifications}
          
      - Identify Content (what sales materials we have)
        Required metadata:
          - topic: "content"
          - content_type: type of content (case study, white paper, etc.)
          - target_audience: [list of target personas]
          - use_case: (optional) specific use case scenario
    
      
    2. Establish Relationships:
      Common relationship patterns include:
      - Persona experiences Pain Point
        Metadata should include:
          - relation_type: "experiences"
          - severity: impact level on the persona
          - frequency: how often this occurs
      
      - Feature solves Pain Point
        Metadata should include:
          - relation_type: "solves"
          - effectiveness: solution effectiveness level
          - implementation_effort: required effort to implement
      
      - Content supports Feature
        Metadata should include:
          - relation_type: "supports"
          - evidence_type: type of supporting evidence
          - effectiveness: how well it demonstrates the feature
      
      - Content targets Persona
        Metadata should include:
          - relation_type: "targets"
          - relevance: how relevant the content is
          - engagement_level: expected level of engagement
          
      - Content addresses Pain Point
        Metadata should include:
          - relation_type: "addresses"
          - solution_approach: how the content addresses the pain point
          - credibility: level of credibility of the solution
    
    Please ensure:
    1. Each entity has the correct required metadata fields as specified above
    2. All relationships have clear source and target entities with correct types
    3. Use consistent entity names across relationships
  
    
    Please only response in JSON format:
    {
        "knowledge": {
            "entities": [
                {
                    "name": "entity name",
                    "description": "detailed description",
                    "metadata": {
                        "topic": "persona|pain_point|feature|content",
                        ... // entity specific required fields
                    }
                }
            ],
            "relationships": [
                {
                    "source_entity": "source entity name",
                    "target_entity": "target entity name",
                    "relationship_desc": "detailed description",
                    "metadata": {
                        "relation_type": "experiences|solves|supports|targets|addresses",
                        ... // relationship specific attributes
                    }
                }
            ]
        }
    }
    """

    text = dspy.InputField(desc="text to extract sales and marketing related entities and relationships")
    knowledge: KnowledgeGraph = dspy.OutputField(desc="Graph representation of the sales and marketing knowledge.")

class PlaybookExtractor:
    """Extractor for sales playbook knowledge graph, using customized prompts for playbook entities and relationships"""
    def __init__(self, dspy_lm: dspy.LM, complied_extract_program_path: Optional[str] = None):
        self.extract_prog = Extractor(dspy_lm=dspy_lm)
        self.extract_prog.prog_graph = TypedPredictor(ExtractPlaybookTriplet)
        if complied_extract_program_path is not None:
            self.extract_prog.load(complied_extract_program_path)
            
    def validate_entity_metadata(self, entity):
        """Validate and normalize entity metadata"""
        metadata = entity.get("metadata", {})
        topic = metadata.get("topic")
        
        if topic == "persona":
            if "industry" not in metadata:
                raise ValueError("Persona entity must have 'industry' field")
        elif topic == "pain_point":
            required_fields = ["scenario", "impact"]
            missing = [f for f in required_fields if f not in metadata]
            if missing:
                raise ValueError(f"Pain point entity missing required fields: {missing}")
        elif topic == "feature":
            if "benefits" not in metadata:
                raise ValueError("Feature entity must have 'benefits' field")
        elif topic == "content":
            required_fields = ["content_type", "target_audience"]
            missing = [f for f in required_fields if f not in metadata]
            if missing:
                raise ValueError(f"Content entity missing required fields: {missing}")
        else:
            raise ValueError(f"Invalid topic: {topic}")
            
        return metadata

    def validate_relationship_metadata(self, relationship):
        """Validate and normalize relationship metadata"""
        metadata = relationship.get("metadata", {})
        relation_type = metadata.get("relation_type")
        
        if relation_type == PlaybookRelationType.EXPERIENCES:
            required_fields = ["severity", "frequency"]
            missing = [f for f in required_fields if f not in metadata]
            if missing:
                raise ValueError(f"Experience relationship missing required fields: {missing}")
                
        elif relation_type == PlaybookRelationType.SOLVES:
            required_fields = ["effectiveness", "implementation_effort"]
            missing = [f for f in required_fields if f not in metadata]
            if missing:
                raise ValueError(f"Solving relationship missing required fields: {missing}")
                
        elif relation_type == PlaybookRelationType.SUPPORTS:
            required_fields = ["evidence_type", "effectiveness"]
            missing = [f for f in required_fields if f not in metadata]
            if missing:
                raise ValueError(f"Support relationship missing required fields: {missing}")
                
        elif relation_type == PlaybookRelationType.TARGETS:
            required_fields = ["relevance", "engagement_level"]
            missing = [f for f in required_fields if f not in metadata]
            if missing:
                raise ValueError(f"Target relationship missing required fields: {missing}")
                
        elif relation_type == PlaybookRelationType.ADDRESSES:
            required_fields = ["solution_approach", "credibility"]
            missing = [f for f in required_fields if f not in metadata]
            if missing:
                raise ValueError(f"Address relationship missing required fields: {missing}")
        else:
            raise ValueError(f"Invalid relation_type: {relation_type}")
            
        return metadata
      
    def extract(self, text: str, node: BaseNode):
        logger.info(f"Start extracting playbook knowledge graph from text: {text}")
        logger.info(f"Node: {node}")
        pred = self.extract_prog(text=text)
        logger.info(f"Pred: {pred}")
        metadata = get_relation_metadata_from_node(node)
        knowledge = self._to_df(
            pred.knowledge.entities, pred.knowledge.relationships, metadata
        )
        # Validate and normalize each entity's metadata
        validated_entities = []
        for entity in knowledge.entities:
            try:
                validated_metadata = self.validate_entity_metadata(entity)
                entity["metadata"] = validated_metadata
                validated_entities.append(entity)
            except ValueError as e:
                logger.warning(f"Skipping invalid entity: {str(e)}")
                
        # Validate and normalize each relationship's metadata
        validated_relationships = []
        for relationship in pred.knowledge.relationships:
            try:                
                if not relationship.metadata.get("relation_type"):
                    raise ValueError("Relationship missing relation_type")
                    
                source_entity = next(
                    (e for e in validated_entities if e.name == relationship.source_entity),
                    None
                )
                target_entity = next(
                    (e for e in validated_entities if e.name == relationship.target_entity),
                    None
                )
                
                if not source_entity or not target_entity:
                    raise ValueError(
                        f"Invalid relationship: source or target entity not found - "
                        f"{relationship.source_entity} -> {relationship.target_entity}"
                    )
                    
                relation_type = relationship.metadata["relation_type"]
                source_topic = source_entity.metadata.get("topic")
                target_topic = target_entity.metadata.get("topic")
                
                valid_combinations = {
                    PlaybookRelationType.EXPERIENCES: ("persona", "pain_point"),
                    PlaybookRelationType.SOLVES: ("feature", "pain_point"),
                    PlaybookRelationType.SUPPORTS: ("content", "feature"),
                    PlaybookRelationType.TARGETS: ("content", "persona"),
                    PlaybookRelationType.ADDRESSES: ("content", "pain_point")
                }
                
                if (source_topic, target_topic) != valid_combinations.get(relation_type):
                    raise ValueError(
                        f"Invalid relationship type {relation_type} between "
                        f"{source_topic} and {target_topic}"
                    )
                    
                validated_metadata = self.validate_relationship_metadata(relationship)
                relationship.metadata = validated_metadata
                
                relationship.metadata.update(metadata)
                
                validated_relationships.append(relationship)
                
            except ValueError as e:
                logger.warning(f"Skipping invalid relationship: {str(e)}")
    
        knowledge = self._to_df(
          validated_entities,
          validated_relationships,
          metadata
        )
        return knowledge

    def _to_df(
        self,
        entities: list[Entity],
        relationships: list[PlaybookRelationship],
        extra_meta: Mapping[str, str],
    ):
        # Create lists to store dictionaries for entities and relationships
        entities_data = []
        relationships_data = []

        # Process entities
        for entity in entities:
            entity_dict = {
                "name": entity.name,
                "description": entity.description,
                "meta": entity.metadata,
            }
            logger.info(f"Entity: {entity_dict}")
            entities_data.append(entity_dict)

        mapped_entities = {entity["name"]: entity for entity in entities_data}

        # Process relationships
        for relationship in relationships:
            source_entity = mapped_entities.get(relationship.source_entity)
            target_entity = mapped_entities.get(relationship.target_entity)
            logger.info(f"Relationship: {relationship}")
            if source_entity and target_entity:
                relationship_dict = {
                    "source_entity": relationship.source_entity,
                    "source_entity_description": source_entity["description"],
                    "target_entity": relationship.target_entity,
                    "target_entity_description": target_entity["description"],
                    "relationship_desc": relationship.relationship_desc,
                    "meta": {
                        **relationship.metadata,
                        **extra_meta,
                    }
                }
                relationships_data.append(relationship_dict)

        # Create DataFrames for entities and relationships
        entities_df = pd.DataFrame(entities_data)
        relationships_df = pd.DataFrame(relationships_data)
        return entities_df, relationships_df