from enum import Enum
from typing import Any, Mapping
from jsonschema import Validator
from pydantic import Field

from backend.app.rag.knowledge_graph.schema import EntityWithID

class Persona(EntityWithID):
    """Represents a persona entity in the knowledge graph with industry and optional role information."""

    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates (which is a comprehensive json TREE, the first field is always: 'topic', "
            "the fields after are always: 'industry', 'role'(optional)) to claim the entity."
        )
    )
    
    @Validator('metadata')
    def validate_persona_metadata(cls, v):
        required_fields = {'topic','industry'}
        missing_fields = required_fields - set(v.keys())
        if missing_fields:
            raise ValueError(f"metadata missing required persona fields: {missing_fields}")
        
        return v

class PainPoint(EntityWithID):
    """Represents a pain point or challenge faced by users."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates (which is a comprehensive json TREE, the first field is always: 'topic', "
            "the fields after are always: 'scenario', 'impact', 'severity'(optional)) to claim the entity."
        )
    )

    @Validator('metadata')
    def validate_painpoint_metadata(cls, v):
        required_fields = {'topic','scenario', 'impact'}
        missing_fields = required_fields - set(v.keys())
        if missing_fields:
            raise ValueError(f"metadata missing required painpoint fields: {missing_fields}")
        
        return v

class Feature(EntityWithID):
    """Represents a product feature or capability."""

    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates (which is a comprehensive json TREE, the first field is always: 'topic', "
            "the fields after are always: 'benefits' (type List[str]), 'technical_details' (type Dict[str])) to claim the entity."
        )
    )

    @Validator('metadata')
    def validate_feature_metadata(cls, v):
        required_fields = {'topic','benefits'}
        missing_fields = required_fields - set(v.keys())
        if missing_fields:
            raise ValueError(f"metadata missing required feature fields: {missing_fields}")
        
        return v


# class ContentType(str, Enum):
#     """Types of content that can be associated with features"""
#     TECHNICAL_DOC = "technical_doc"
#     CASE_STUDY = "case_study"
#     SOLUTION = "solution" 
#     BLOG = "blog"
#     WHITEPAPER = "whitepaper"

# class Content(EntityWithID):
#     """Represents content materials related to features, such as technical docs, case studies, solutions etc."""

#     content_type: ContentType = Field(
#         description="The type of the content material"
#     )

#     url: str = Field(
#         description="The URL or path where this content can be accessed"
#     )

#     language: str = Field(
#         description="The language of the content, e.g., 'en', 'zh-CN'"
#     )

#     marketing_level: Optional[str] = Field(
#         default=None,
#         description="Marketing level of the content: 'High', 'Medium', 'Low', or None"
#     )

#     target_audience: Optional[List[str]] = Field(
#         default_factory=list,
#         description="Target audience groups for this content"
#     )

#     publish_date: Optional[datetime] = Field(
#         default=None,
#         description="The publication date of the content"
#     )

#     authors: Optional[List[str]] = Field(
#         default_factory=list,
#         description="List of authors or contributors"
#     )

#     tags: Optional[List[str]] = Field(
#         default_factory=list,
#         description="List of tags or keywords associated with the content"
#     )

#     metrics: Optional[Dict] = Field(
#         default_factory=dict,
#         description="Content performance metrics, e.g., views, downloads, ratings"
#     )