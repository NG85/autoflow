from typing import Any, Mapping
from pydantic import Field

from app.rag.knowledge_graph.schema import Entity

class Persona(Entity):
    """Represents a persona entity in the knowledge graph with detailed business characteristics."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the persona entity including:\n"
            "- topic: Always 'persona'\n"
            "- industry: Target industry of the persona\n"
            "- role: (optional) Specific role or position"
        ),
        json_schema_extra={
            "required": ["topic", "industry"],
            "properties": {
                "topic": {"type": "string"},
                "industry": {"type": "string"},
                "role": {"type": "string"},
            }
        }
    )

   
class PainPoint(Entity):
    """Represents a pain point entity with comprehensive business impact analysis."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the pain point entity including:\n"
            "- topic: Always 'pain_point'\n"
            "- scenario: Specific context or situation\n"
            "- impact: Business or operational impact\n"
            "- severity: (optional) Level of severity"
        ),
        json_schema_extra={
            "required": ["topic", "scenario", "impact"],
            "properties": {
                "topic": {"type": "string"},
                "scenario": {"type": "string"},
                "impact": {"type": "string"},
                "severity": {"type": "string"}
            }
        }
    )


class Feature(Entity):
    """Represents a product/service feature with detailed technical and business benefits."""

    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the feature entity including:\n"
            "- topic: Always 'feature'\n"
            "- benefits: List of specific business benefits\n"
            "- technical_details: Technical specifications and requirements"
        ),
        json_schema_extra={
            "required": ["topic", "benefits"],
            "properties": {
                "topic": {"type": "string"},
                "benefits": {"type": "array", "items": {"type": "string"}},
                "technical_details": {
                    "type": "object",
                    "additionalProperties": {"type": "string"}
                }
            }
        }
    )


class Content(Entity):
    """Represents sales content with detailed targeting and usage information."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the content entity including:\n"
            "- topic: Always 'content'\n"
            "- content_type: Type of sales material\n"
            "- target_audience: List of intended personas\n"
            "- use_case: Specific usage scenarios"
        ),
        json_schema_extra={
            "required": ["topic", "content_type", "target_audience"],
            "properties": {
                "topic": {"type": "string"},
                "content_type": {"type": "string"},
                "target_audience": {"type": "array"},
                "use_case": {"type": "string"}
            }
        }
    )