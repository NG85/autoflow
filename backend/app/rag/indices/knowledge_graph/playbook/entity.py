from typing import Any, Mapping
from pydantic import Field

from app.rag.indices.knowledge_graph.schema import Entity

class Persona(Entity):
    """Represents a persona entity in the knowledge graph with detailed business characteristics."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the persona entity including:\n"
            "- topic: Always 'persona'\n"
            "- industry: Target industry of the organization\n"
            "- persona_type: Type of organization or department (e.g., 'Enterprise Company', 'IT Department', 'Security Team')\n"
            "- role: (Optional) Object containing role information:\n"
            "  - title: Job position or title (use 'Unspecified Role' if not explicitly mentioned)\n"
            "  - level: One of ['c_level', 'middle_management', 'operational_staff']"
        ),
        json_schema_extra={
            "required": ["topic", "industry", "persona_type", "role", "role_level"],
            "properties": {
                "topic": {"type": "string", "const": "persona"},
                "industry": {"type": "string"},
                "persona_type": {"type": "string"},
                "role": {
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
            "- source: Product source ('own' or competitor product name), defaults to 'own' for backward compatibility\n"
            "- benefits: List of specific business benefits\n"
            "- technical_details: (optional) Technical specifications and requirements"
        ),
        json_schema_extra={
            "required": ["topic", "benefits"],
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
            "- domain: domain of the case (industry + business scenario, e.g. 'financial risk control')\n"
            "- features: list of related features/products\n"
            "- outcomes: implementation results (must include quantifiable metrics)\n"
            "- references: (optional) reference customer/implementation period information"
        ),
        json_schema_extra={
            "required": ["topic", "domain", "features", "outcomes"],
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
            "- name: Product name (e.g., 'MongoDB Atlas', 'Oracle Database')\n"
            "- company: Company that owns the product\n"
            "- category: Product category (e.g., 'Database', 'Data Migration Tool')\n"
        ),
        json_schema_extra={
            "required": ["topic", "name", "company", "category"],
            "properties": {
                "topic": {"type": "string", "const": "competitor"},
                "name": {"type": "string"},
                "company": {"type": "string"},
                "category": {"type": "string"}
            }
        }
    )