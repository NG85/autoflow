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
        ),
        json_schema={
            "type": "object",
            "required": ["topic", "industry"],
            "properties": {
                "topic": {"type": "string"},
                "industry": {"type": "string"},
                "role": {"type": "string"}
            }
        }
    )
   
    @Validator('metadata')
    def validate_persona_metadata(cls, v):
        if 'industry' not in v:
            raise ValueError("metadata missing required persona field: industry")
        return v

class PainPoint(EntityWithID):
    """Represents a pain point or challenge faced by users."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates (which is a comprehensive json TREE, the first field is always: 'topic', "
            "the fields after are always: 'scenario', 'impact', 'severity'(optional)) to claim the entity."
        ),
        json_schema={
            "type": "object",
            "required": ["topic", "scenario", "impact"],
            "properties": {
                "topic": {"type": "string"},
                "scenario": {"type": "string"},
                "impact": {"type": "string"},
                "severity": {"type": "string"}
            }
        }
    )

    @Validator('metadata')
    def validate_painpoint_metadata(cls, v):
        required_fields = {'scenario', 'impact'}
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
        ),
        json_schema={
            "type": "object",
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

    @Validator('metadata')
    def validate_feature_metadata(cls, v):
        if 'benefits' not in v:
            raise ValueError("metadata missing required feature field: benefits")
        return v
