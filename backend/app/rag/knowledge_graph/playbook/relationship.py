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
