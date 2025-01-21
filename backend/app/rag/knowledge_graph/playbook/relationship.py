from enum import Enum
from typing import Any, Mapping

from pydantic import Field
from app.rag.knowledge_graph.schema import Relationship

class PlaybookRelationType(str, Enum):
    """Enum for playbook specific relationship types"""
    EXPERIENCES = "experiences"     # Persona -> experiences -> Pain Point
    SOLVES = "solves"              # Feature -> solves -> Pain Point
    SUPPORTS = "supports"          # Content -> supports -> Feature
    TARGETS = "targets"           # Content -> targets -> Persona
    ADDRESSES = "addresses"       # Content -> addresses -> Pain Point

class PlaybookRelationship(Relationship):
    """List of relationships extracted from the text to form the playbook knowledge graph"""

    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates (which is a comprehensive json TREE) to claim the relationship. "
        )
    )
    
class ExperienceRelation(PlaybookRelationship):
    """Represents how a persona experiences a pain point."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The metadata for experience relationship including:\n"
            "- relation_type: Always 'experiences'\n"
            "- severity: Impact level on the persona\n"
            "- frequency: How often this occurs\n"
            "- business_impact: Business implications of this experience\n"
            "- priority_level: Priority for addressing this pain point"
        ),
        json_schema_extra={
            "required": ["relation_type", "severity", "frequency"],
            "properties": {
                "relation_type": {"const": "experiences"},
                "severity": {"type": "string"},
                "frequency": {"type": "string"},
                "business_impact": {"type": "string"},
                "priority_level": {"type": "string"}
            }
        }
    )

class SolvingRelation(PlaybookRelationship):
    """Represents how a feature solves a pain point."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The metadata for solving relationship including:\n"
            "- relation_type: Always 'solves'\n"
            "- effectiveness: Solution effectiveness level\n"
            "- implementation_effort: Required effort to implement\n"
            "- time_to_value: Expected time to realize benefits\n"
            "- success_criteria: Criteria for measuring success"
        ),
        json_schema_extra={
            "required": ["relation_type", "effectiveness", "implementation_effort"],
            "properties": {
                "relation_type": {"const": "solves"},
                "effectiveness": {"type": "string"},
                "implementation_effort": {"type": "string"},
                "time_to_value": {"type": "string"},
                "success_criteria": {"type": "string"}
            }
        }
    )

class SupportRelation(PlaybookRelationship):
    """Represents how content supports/demonstrates a feature."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The metadata for support relationship including:\n"
            "- relation_type: Always 'supports'\n"
            "- evidence_type: Type of supporting evidence\n"
            "- effectiveness: How well it demonstrates the feature\n"
            "- use_case_fit: How well it fits specific use cases\n"
            "- content_quality: Quality rating of the supporting content"
        ),
        json_schema_extra={
            "required": ["relation_type", "evidence_type", "effectiveness"],
            "properties": {
                "relation_type": {"const": "supports"},
                "evidence_type": {"type": "string"},
                "effectiveness": {"type": "string"},
                "use_case_fit": {"type": "string"},
                "content_quality": {"type": "string"}
            }
        }
    )

class TargetRelation(PlaybookRelationship):
    """Represents how content targets a specific persona."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The metadata for target relationship including:\n"
            "- relation_type: Always 'targets'\n"
            "- relevance: How relevant the content is\n"
            "- engagement_level: Expected level of engagement\n"
            "- content_effectiveness: How effective the content is\n"
            "- conversion_potential: Potential for converting the persona"
        ),
        json_schema_extra={
            "required": ["relation_type", "relevance", "engagement_level"],
            "properties": {
                "relation_type": {"const": "targets"},
                "relevance": {"type": "string"},
                "engagement_level": {"type": "string"},
                "content_effectiveness": {"type": "string"},
                "conversion_potential": {"type": "string"}
            }
        }
    )

class AddressRelation(PlaybookRelationship):
    """Represents how content addresses a pain point."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The metadata for address relationship including:\n"
            "- relation_type: Always 'addresses'\n"
            "- solution_approach: How the content addresses the pain point\n"
            "- credibility: Level of credibility of the solution\n"
            "- impact_assessment: Assessment of solution impact\n"
            "- implementation_guidance: Guidance on implementing the solution"
        ),
        json_schema_extra={
            "required": ["relation_type", "solution_approach", "credibility"],
            "properties": {
                "relation_type": {"const": "addresses"},
                "solution_approach": {"type": "string"},
                "credibility": {"type": "string"},
                "impact_assessment": {"type": "string"},
                "implementation_guidance": {"type": "string"}
            }
        }
    )