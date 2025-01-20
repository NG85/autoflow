from typing import Optional
import dspy
from dspy.functional import TypedPredictor

from app.rag.knowledge_graph.extractor import Extractor, SimpleGraphExtractor
from app.rag.knowledge_graph.schema import KnowledgeGraph

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
                        "topic": "persona|pain_point|feature",
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
                        "relation_type": "experiences|solves",
                        ... // relationship specific attributes
                    }
                }
            ]
        }
    }
    """

    text = dspy.InputField(desc="text to extract sales and marketing related entities and relationships")
    knowledge: KnowledgeGraph = dspy.OutputField(desc="Graph representation of the sales and marketing knowledge.")

class PlaybookExtractor(SimpleGraphExtractor):
    """Extractor for sales playbook knowledge graph, using customized prompts for playbook entities and relationships"""
    def __init__(self, dspy_lm: dspy.LM, complied_extract_program_path: Optional[str] = None):
        self.extract_prog = Extractor(dspy_lm=dspy_lm)
        self.extract_prog.prog_graph = TypedPredictor(ExtractPlaybookTriplet)
        if complied_extract_program_path is not None:
            self.extract_prog.load(complied_extract_program_path)
