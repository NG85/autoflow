from typing import Any, Mapping
from pydantic import Field

from app.rag.indices.knowledge_graph.schema import Entity

class Case(Entity):
    """Represents a legal case entity."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the case entity including:\n"
            "- topic: Always 'case'\n"
            "- title: Case title\n"
            "- dateFiled: Filing date\n"
            "- dateJudged: Judgment date"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "case"},
                "title": {"type": "string"},
                "dateFiled": {"type": "string", "format": "date"},
                "dateJudged": {"type": "string", "format": "date"}
            }
        }
    )

class Court(Entity):
    """Represents a court entity."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the court entity including:\n"
            "- topic: Always 'court'\n"
            "- name: Full name of the court\n"
            "- level: Court level (e.g., 'basic', 'intermediate', 'high')"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "court"},
                "name": {"type": "string"},
                "level": {"type": "string"}
            }
        }
    )

class Region(Entity):
    """Represents a region entity."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the region entity including:\n"
            "- topic: Always 'region'\n"
            "- name: Region name (province/autonomous region/municipality, city, county/district)"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "region"},
                "name": {"type": "string"}
            }
        }
    )

class Party(Entity):
    """Represents a party entity in a legal case."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the party entity including:\n"
            "- topic: Always 'party'\n"
            "- name: Party name\n"
            "- role: Party role (plaintiff/defendant/third party)\n"
            "- type: Party type (worker/employer/other)"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "party"},
                "name": {"type": "string"},
                "role": {"type": "string"},
                "type": {"type": "string"}
            }
        }
    )

class Claim(Entity):
    """Represents a claim entity in a legal case."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the claim entity including:\n"
            "- topic: Always 'claim'\n"
            "- description: Claim content (subject matter, compensation amount, etc.)"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "claim"},
                "description": {"type": "string"}
            }
        }
    )

class Fact(Entity):
    """Represents a fact entity in a legal case."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the fact entity including:\n"
            "- topic: Always 'fact'\n"
            "- description: Fact description\n"
            "- dateOccurred: Date when the fact occurred"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "fact"},
                "description": {"type": "string"},
                "dateOccurred": {"type": "string", "format": "date"}
            }
        }
    )

class Issue(Entity):
    """Represents an issue entity in a legal case."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the issue entity including:\n"
            "- topic: Always 'issue'\n"
            "- description: Issue description (whether facts are established, whether law is applicable)"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "issue"},
                "description": {"type": "string"}
            }
        }
    )

class Viewpoint(Entity):
    """Represents a viewpoint entity in a legal case."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the viewpoint entity including:\n"
            "- topic: Always 'viewpoint'\n"
            "- side: Party side (plaintiff/defendant)\n"
            "- content: Key points of the viewpoint\n"
            "- judgment: Judgment result (win/lose)"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "viewpoint"},
                "side": {"type": "string"},
                "content": {"type": "string"},
                "judgment": {"type": "string"}
            }
        }
    )

class JudgmentOpinion(Entity):
    """Represents a judgment opinion entity in a legal case."""
    
    metadata: Mapping[str, Any] = Field(
        description=(
            "The covariates to claim the judgment opinion entity including:\n"
            "- topic: Always 'judgment_opinion'\n"
            "- content: Key points of the court's reasoning"
        ),
        json_schema_extra={
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string", "const": "judgment_opinion"},
                "content": {"type": "string"}
            }
        }
    )