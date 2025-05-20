from typing import Any, Mapping
from pydantic import Field

from app.rag.indices.knowledge_graph.schema import Entity

class Persona(Entity):
    """Represents a persona entity in the knowledge graph with detailed business characteristics."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the persona entity including:\n"
            "- topic: Always 'persona'\n"
            "- industry: (Optional) Target industry of the organization\n"
            "- persona_type: (Optional) Type of organization or department (e.g., 'Enterprise Company', 'IT Department', 'Security Team')\n"
            "- role: (Optional) Object containing role information:\n"
            "  - title: (Optional) Job position or title (use 'Unspecified Role' if not explicitly mentioned)\n"
            "  - level: (Optional) One of ['c_level', 'middle_management', 'operational_staff']"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "persona"},
                "industry": {"type": "string"},
                "persona_type": {"type": "string"},
                "role": {
                    "type": "object",
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
    )

   
class PainPoint(Entity):
    """Represents a pain point entity with comprehensive business impact analysis."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the pain point entity including:\n"
            "- topic: Always 'pain_point'\n"
            "- scenario: (Optional) Specific context or situation\n"
            "- impact: (Optional) Business or operational impact\n"
            "- severity: (Optional) Level of severity"
        ),
        json_schema_extra={
            "required": ["topic"],
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
            "- source: (Optional) Product source ('own' or competitor product name), defaults to 'own' for backward compatibility\n"
            "- benefits: (Optional) List of specific business benefits\n"
            "- technical_details: (Optional) Technical specifications and requirements"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string"},
                "source": {"type": "string", "default": "own"},
                "benefits": {"type": "array", "items": {"type": "string"}},
                "technical_details": {
                    "type": "object",
                    "additionalProperties": {"type": "string"}
                }
            }
        }
    )

class Cases(Entity):
    """Represents a case entity"""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the case entity including:\n"
            "- topic: Always 'case'\n"
            "- domain: (Optional) domain of the case (industry + business scenario, e.g. 'financial risk control')\n"
            "- features: (Optional) list of related features/products\n"
            "- outcomes: (Optional) implementation results (must include quantifiable metrics)\n"
            "- references: (Optional) reference customer/implementation period information"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "case"},
                "domain": {"type": "string"},
                "features": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "outcomes": {"type": "string"},
                "references": {"type": "string"}
            }
        }
    )

class Competitor(Entity):
    """Represents a competitor product entity."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the competitor product entity including:\n"
            "- topic: Always 'competitor'\n"
            "- name: (Optional) Product name (e.g., 'MongoDB Atlas', 'Oracle Database')\n"
            "- company: (Optional) Company that owns the product\n"
            "- category: (Optional) Product category (e.g., 'Database', 'Data Migration Tool')\n"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "competitor"},
                "name": {"type": "string"},
                "company": {"type": "string"},
                "category": {"type": "string"}
            }
        }
    )