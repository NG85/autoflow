from typing import Any, Mapping
from pydantic import Field

from app.rag.knowledge_graph.schema import Entity

class Persona(Entity):
    """Represents a persona entity in the knowledge graph with detailed business characteristics."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the persona entity including:\n"
            "- topic: Always 'persona'\n"
            "- industry: Target industry of the organization\n"
            "- persona_type: Type of the organization or department\n"
            "  Example: 'Enterprise Company', 'IT Department', 'Security Team'\n"
            "- roles: (optional) List of key roles within this persona, each with:\n"
            "  * title: Role description or job title\n"
            "  * level: One of ['c_level', 'middle_management', 'operational_staff']"
        ),
        json_schema_extra={
            "required": ["topic", "industry", "persona_type"],
            "properties": {
                "topic": {"type": "string", "const": "persona"},
                "industry": {"type": "string"},
                "persona_type": {"type": "string"},
                "roles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["title", "level"],
                        "properties": {
                            "title": {"type": "string"},
                            "level": {
                                "type": "string",
                                "enum": ["c_level", "middle_management", "operational_staff"]
                            }
                        }
                    }
                }
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