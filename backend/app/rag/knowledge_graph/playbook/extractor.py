from typing import Optional
import dspy
from dspy.functional import TypedPredictor

from app.rag.knowledge_graph.extractor import Extractor as BaseExtractor
from app.rag.knowledge_graph.schema import EntityCovariateInput, KnowledgeGraph

class ExtractPlaybookTriplet(dspy.Signature):
    """Carefully analyze the provided text to identify sales and marketing related entities and their relationships.
    
    Follow these Step-by-Step Analysis:

    1. Extract Key Entities:
      - Identify Personas (who are the target users/customers)
        Metadata should include: industry, role, pain_points
      - Identify Pain Points (what problems they face)
        Metadata should include: scenario, impact, severity
      - Identify Features (what solutions we offer)
        Metadata should include: benefits, technical_details
      
    2. Establish Relationships:
      When identifying relationships, please specify:
      - The relationship type in the metadata
      - Any relevant attributes in the metadata
      
      Common relationship patterns include:
      - Persona experiences Pain Point
        Include severity and impact in metadata
      - Feature solves Pain Point
        Include effectiveness level in metadata
      
    Please ensure all relationships have:
    1. Clear source and target entities
    2. Detailed relationship description
    3. Metadata containing:
       - relation_type: the type of relationship
       - Any additional attributes specific to that relationship type
    
    Please only response in JSON format with the following structure:
    ```json
    {
        "knowledge": {
            "entities": [
                {
                    "name": "entity name",
                    "description": "detailed description",
                    "metadata": {
                        "topic": "entity category",
                        ... // other metadata fields
                    }
                }
            ],
            "relationships": [
                {
                    "source_entity": "source entity name",
                    "target_entity": "target entity name",
                    "relationship_desc": "detailed description",
                    "metadata": {
                        "relation_type": "type of relationship",
                        ... // relationship specific attributes
                    }
                }
            ]
        }
    }
    ```
    """

    text = dspy.InputField(desc="text to extract sales and marketing related entities and relationships")
    knowledge: KnowledgeGraph = dspy.OutputField(desc="Graph representation of the sales and marketing knowledge.")

class PlaybookExtractor(BaseExtractor):
    """Extractor for sales playbook related entities and relationships."""
    
    def __init__(self, dspy_lm: dspy.LM):
        super().__init__(dspy_lm)
        self.prog_graph = TypedPredictor(ExtractPlaybookTriplet)

    def forward(self, text: str):
        """Extract playbook related entities and relationships from text."""
        with dspy.settings.context(lm=self.dspy_lm):
            pred_graph = self.prog_graph(
                text=text,
                config=self.get_llm_output_config(),
            )

            entities_for_covariates = [
                EntityCovariateInput(
                    name=entity.name,
                    description=entity.description,
                )
                for entity in pred_graph.knowledge.entities
            ]

            pred_covariates = self.prog_covariates(
                text=text,
                entities=entities_for_covariates,
                config=self.get_llm_output_config(),
            )

            for entity in pred_graph.knowledge.entities:
                for covariate in pred_covariates.covariates:
                    if entity.name == covariate.name:
                        entity.metadata.update(covariate.covariates)

            return pred_graph