from enum import Enum
from typing import List, Optional

from sqlmodel import Field
from app.rag.knowledge_graph.schema import Relationship, RelationshipReasoning

class PlaybookRelationType(str, Enum):
    """Enum for playbook specific relationship types"""
    EXPERIENCES = "experiences"     # Persona -> experiences -> Pain Point
    SOLVES = "solves"              # Feature -> solves -> Pain Point

class PlaybookRelationship(Relationship):
    """Base class for playbook specific relationships"""
    
    relation_type: PlaybookRelationType = Field(
        description="Type of the relationship in playbook context"
    )

class ExperienceRelation(PlaybookRelationship):
    """Represents a persona experiencing a pain point"""
    
    relation_type: PlaybookRelationType = PlaybookRelationType.EXPERIENCES

class SolvingRelation(PlaybookRelationship):
    """Represents a feature solving a pain point"""
    
    relation_type: PlaybookRelationType = PlaybookRelationType.SOLVES
    effectiveness: Optional[str] = Field(
        default=None,
        description="How effectively the feature solves the pain point (e.g., 'High', 'Medium', 'Low')"
    )

# class ContentRelationType(str, Enum):
#     """Types of relationships that Content can have"""
#     DOCUMENTS = "documents"          # Content -> documents -> Feature
#     DEMONSTRATES = "demonstrates"    # Content -> demonstrates -> Feature
#     REFERENCES = "references"        # Content -> references -> Feature
#     SUPPORTS = "supports"           # Content -> supports -> SalesPitch
#     REINFORCES = "reinforces"       # Content -> reinforces -> SalesPitch


# class ContentRelationship(PlaybookRelationship):
#     """Base class for content-specific relationships"""
    
#     relation_type: ContentRelationType = Field(
#         description="Type of the content relationship"
#     )
#     relevance: Optional[float] = Field(
#         default=None,
#         description="Relevance score of the content to the target (0.0 to 1.0)"
#     )
#     context: Optional[str] = Field(
#         default=None,
#         description="Additional context about how the content relates to the target"
#     )