import logging
from copy import deepcopy
import pandas as pd
import dspy
from typing import Mapping, Optional, List

from dspy import Predict
from llama_index.core.schema import BaseNode

from app.rag.indices.knowledge_graph.schema import (
    Entity,
    Relationship,
    KnowledgeGraph,
    EntityCovariateInput,
    EntityCovariateOutput,
)
from app.models.enums import GraphType

logger = logging.getLogger(__name__)


class ExtractGraphTriplet(dspy.Signature):
    """Carefully analyze the provided text to thoroughly identify all relevant entities and their relationships, including both general concepts and specific details.

    IMPORTANT: All extracted entities, relationships, and descriptions must strictly preserve the original language of the input text. Do NOT translate or switch languages under any circumstances. If the input is in Chinese, output must be in Chinese; if in English, output must be in English, etc.
    This includes preserving all symbols, units, code snippets, or technical notations as-is in the original text.

    Follow these Step-by-Step Analysis:

    1. Extract Meaningful Entities:
      - Identify all significant nouns, proper nouns, and terminologies that represent key concepts, objects, components, features, processes, steps, use cases, or any substantial entities.
      - Ensure that you capture entities across different levels of detail, from high-level overviews to specific details, to create a comprehensive representation of the subject matter.
      - Pay special attention to domain-specific terms and unique identifiers (e.g., model names, algorithm types, standard codes, etc.) that are critical for precise semantic linking and retrieval.
      - Choose names for entities that are specific enough to indicate their meaning without additional context, avoiding overly generic terms.
      - Consolidate similar entities to avoid redundancy, ensuring each represents a distinct concept at appropriate granularity levels.
      - Always preserve the original language of the input text for all extracted content.

    2. Extract Metadata to claim the entities:
      - Carefully review the provided text, focusing on identifying detailed covariates associated with each entity.
      - Extract and link the covariates (which is a comprehensive json TREE, the first field is always: "topic") to their respective entities.
      - Ensure all extracted covariates are clearly connected to the correct entity for accuracy and comprehensive understanding.
      - Ensure that all extracted covariates are factual and verifiable within the text itself, without relying on external knowledge or assumptions.
      - Collectively, the covariates should provide a thorough and precise summary of the entity's characteristics as described in the source material.
      - Always preserve the original language of the input text for all extracted content.

    3. Establish Relationships:
      - Carefully examine the text to identify all relationships between clearly-related entities, ensuring each relationship is correctly captured with accurate details about the interactions.
      - Analyze the context and interactions between the identified entities to determine how they are interconnected, focusing on actions, associations, dependencies, or similarities.
      - Valid relationship types include but are not limited to: "composed_of", "used_for", "depends_on", "related_to", "implemented_by", "includes", etc.
      - Ensure accurate directionality that reflects logical or functional dependencies among entities.
      - Always preserve the original language of the input text for all extracted content.

    Some key points to consider:
      - Please endeavor to extract all meaningful entities and relationships from the text, avoid subsequent additional gleanings.

    Objective: Produce a detailed and comprehensive knowledge graph that captures the full spectrum of entities mentioned in the text, along with their interrelations, reflecting both broad concepts and intricate details of the subject matter.

    Please only respond in JSON format.
    """

    text = dspy.InputField(
        desc="a paragraph of text to extract entities and relationships to form a knowledge graph"
    )
    knowledge: KnowledgeGraph = dspy.OutputField(
        desc="Graph representation of the knowledge extracted from the text."
    )


class ExtractCovariate(dspy.Signature):
    """Please carefully review the provided text and entities list which are already identified in the text. Focusing on identifying detailed covariates associated with each entities provided.

    IMPORTANT: All extracted covariates, entity names, and descriptions must strictly preserve the original language of the input text. Do NOT translate or switch languages under any circumstances. If the input is in Chinese, output must be in Chinese; if in English, output must be in English, etc.
    This includes preserving all symbols, units, code snippets, or technical notations as-is in the original text.

    Extract and link the covariates (which is a comprehensive json TREE, the first field is always: "topic") to their respective entities.
    Ensure all extracted covariates are clearly connected to the correct entity for accuracy and comprehensive understanding.
    Ensure that all extracted covariates are factual and verifiable within the text itself, without relying on external knowledge or assumptions.
    Collectively, the covariates should provide a thorough and precise summary of the entity's characteristics as described in the source material.

    Please only respond in JSON format.
    """

    text = dspy.InputField(
        desc="a paragraph of text to extract covariates to claim the entities."
    )

    entities: List[EntityCovariateInput] = dspy.InputField(
        desc="List of entities identified in the text."
    )
    covariates: List[EntityCovariateOutput] = dspy.OutputField(
        desc="Graph representation of the knowledge extracted from the text."
    )


def get_relation_metadata_from_node(node: BaseNode):
    metadata = deepcopy(node.metadata)
    for key in [
        "_node_content",
        "_node_type",
        "excerpt_keywords",
        "questions_this_excerpt_can_answer",
        "section_summary",
    ]:
        metadata.pop(key, None)
    metadata["chunk_id"] = node.node_id
    return metadata


class Extractor(dspy.Module):
    def __init__(self, dspy_lm: dspy.LM):
        super().__init__()
        self.dspy_lm = dspy_lm
        self.prog_graph = Predict(ExtractGraphTriplet)
        self.prog_covariates = Predict(ExtractCovariate)

    def forward(self, text):
        with dspy.settings.context(lm=self.dspy_lm):
            pred_graph = self.prog_graph(text=text)
                    
            logger.debug(f"Debug: Predicted graph output: {pred_graph}")
            # extract the covariates
            entities_for_covariates = [
                EntityCovariateInput(
                    name=entity.name,
                    description=entity.description,
                )
                for entity in pred_graph.knowledge.entities
            ]

            try:
                pred_covariates = self.prog_covariates(
                    text=text,
                    entities=entities_for_covariates,
                )
                logger.debug((f"Debug: prog_covariates output before JSON parsing: {pred_covariates}"))
            except Exception as e:
                logger.error(f"Error in prog_covariates: {e}")
                raise e

            # replace the entities with the covariates
            for entity in pred_graph.knowledge.entities:
                if pred_covariates.covariates is not None:
                    for covariate in pred_covariates.covariates:
                        if entity.name == covariate.name:
                            entity.metadata = covariate.covariates

            return pred_graph


class SimpleGraphExtractor:
    def __init__(
        self, dspy_lm: dspy.LM, complied_extract_program_path: Optional[str] = None, graph_type: GraphType = GraphType.general
    ):
        self.graph_type = graph_type
        self.extract_prog = Extractor(dspy_lm=dspy_lm)
        if complied_extract_program_path is not None:
            self.extract_prog.load(complied_extract_program_path)

    def extract(self, text: str, node: BaseNode):
        logger.info(f"Extracting {self.graph_type} knowledge graph from text")
        pred = self.extract_prog(text=text)
        logger.info(f"pred output: {pred}")
        metadata = get_relation_metadata_from_node(node)

        # Ensure all entities have proper metadata dictionary structure
        for entity in pred.knowledge.entities:
            if entity.metadata is None or not isinstance(entity.metadata, dict):
                entity.metadata = {"topic": "Unknown", "status": "auto-generated"}

        return self._to_df(
            pred.knowledge.entities, pred.knowledge.relationships, metadata
        )

    def _to_df(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
        extra_meta: Mapping[str, str],
    ):
        # Create lists to store dictionaries for entities and relationships
        entities_data = []
        relationships_data = []

        # Iterate over parsed entities and relationships to create dictionaries
        for entity in entities:
            entity_dict = {
                "name": entity.name,
                "description": entity.description,
                "meta": entity.metadata,
            }
            entities_data.append(entity_dict)

        mapped_entities = {entity["name"]: entity for entity in entities_data}

        for relationship in relationships:
            source_entity_description = ""
            if relationship.source_entity not in mapped_entities:
                new_source_entity = {
                    "name": relationship.source_entity,
                    "description": (
                        f"Derived from from relationship: "
                        f"{relationship.source_entity} -> {relationship.relationship_desc} -> {relationship.target_entity}"
                    ),
                    "meta": {"status": "need-revised"},
                }
                entities_data.append(new_source_entity)
                mapped_entities[relationship.source_entity] = new_source_entity
                source_entity_description = new_source_entity["description"]
            else:
                source_entity_description = mapped_entities[relationship.source_entity][
                    "description"
                ]

            target_entity_description = ""
            if relationship.target_entity not in mapped_entities:
                new_target_entity = {
                    "name": relationship.target_entity,
                    "description": (
                        f"Derived from from relationship: "
                        f"{relationship.source_entity} -> {relationship.relationship_desc} -> {relationship.target_entity}"
                    ),
                    "meta": {"status": "need-revised"},
                }
                entities_data.append(new_target_entity)
                mapped_entities[relationship.target_entity] = new_target_entity
                target_entity_description = new_target_entity["description"]
            else:
                target_entity_description = mapped_entities[relationship.target_entity][
                    "description"
                ]

            relationship_dict = {
                "source_entity": relationship.source_entity,
                "source_entity_description": source_entity_description,
                "target_entity": relationship.target_entity,
                "target_entity_description": target_entity_description,
                "relationship_desc": relationship.relationship_desc,
                "meta": {
                    **extra_meta,
                },
            }
            relationships_data.append(relationship_dict)

        # Create DataFrames for entities and relationships
        entities_df = pd.DataFrame(entities_data)
        relationships_df = pd.DataFrame(relationships_data)
        return entities_df, relationships_df
