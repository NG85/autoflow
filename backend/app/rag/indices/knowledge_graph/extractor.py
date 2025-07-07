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
    """Carefully analyze the provided labor law-related text (including laws and regulations, legal Q&A, case interpretations, court judgments, etc.) to thoroughly identify all important entities and their relationships, covering both general concepts and specific details.

    Follow these step-by-step instructions:

    1. Extract meaningful entities:
      - Identify all important nouns, proper nouns, and terms related to labor law, including but not limited to: legal provisions, regulation names, parties (such as employers, employees), actions (such as signing or terminating contracts), rights, obligations, responsibilities, processes, scenarios, cases, judgment results, etc.
      - Ensure entities are captured at different levels of detail, from high-level legal rules to specific case details, to form a comprehensive representation of labor law knowledge.
      - Entity names should be specific and clear, avoiding overly generic terms, and should be understandable even without additional context.
      - Consolidate similar entities to avoid redundancy, ensuring each entity represents a distinct and meaningful labor law concept or object.

    2. Extract entity metadata (attributes):
      - Carefully review the text to identify detailed attributes related to each entity (such as article numbers, applicable conditions, relevant dates, amounts, subject qualifications, case background, judgment basis, etc.).
      - Extract and associate these attributes with the corresponding entity in a structured JSON tree (the first field should always be "topic").
      - Ensure all attributes are verifiable within the original text, without introducing external knowledge or assumptions.
      - Each attribute should be accurately linked to its entity, fully reflecting the characteristics and descriptions of the entity in the text.

    3. Establish relationships between entities:
      - Carefully analyze the text to identify various relationships between entities, including but not limited to: rights and obligations, causality, applicability, constraints, judgment relationships in cases, etc.
      - Analyze the context and interactions between entities, clarify the directionality of relationships, and accurately describe the logical or legal dependencies (e.g., "the employer is obliged to pay wages to the employee", "a certain law applies to a certain employment scenario").
      - Relationship descriptions should be specific and reflect the practical application and logic of labor law.

    Some key points to consider:
      - Strive to extract all meaningful labor law-related entities and relationships as comprehensively as possible, avoiding omissions.

    Objective: Generate a detailed and structured labor law knowledge graph covering all entities and their relationships mentioned in the text, providing a solid foundation for subsequent knowledge-graph-based Q&A (such as enterprise compliance employment consulting).

    Please only respond in JSON format.
    """

    text = dspy.InputField(
        desc="a paragraph of text to extract entities and relationships to form a knowledge graph"
    )
    knowledge: KnowledgeGraph = dspy.OutputField(
        desc="Graph representation of the knowledge extracted from the text."
    )


class ExtractCovariate(dspy.Signature):
    """Carefully review the provided labor law-related text and the list of identified entities, focusing on extracting detailed covariates (attributes) associated with each entity.

    - For each entity, extract and associate detailed attributes (such as article numbers, applicable conditions, relevant dates, amounts, subject qualifications, case background, judgment basis, etc.).
    - Covariates should be organized as a structured JSON tree, with the first field always being "topic", and other fields extracted according to the text. Ensure all attributes are verifiable within the original text, without introducing external knowledge or assumptions.
    - Each covariate should be accurately linked to its entity, ensuring the correspondence between attributes and entities is clear, accurate, and comprehensive.
    - All covariates should be factual and verifiable, fully reflecting the characteristics and descriptions of the entity in the text.

    Objective: Generate detailed and structured attribute information for each labor law-related entity, providing a solid foundation for subsequent knowledge graph and Q&A applications.

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
