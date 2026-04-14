from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.rag.chat.review.intent_router import ReviewIntent, ReviewSessionContext
from app.rag.chat.review.data_retriever import ReviewDataContext


AgentTaskType = Literal[
    "intent_classification",
    "structured_data_retrieval",
    "kg_retrieval",
    "vector_retrieval",
    "reasoning",
    "response_generation",
]


class AgentTask(BaseModel):
    task_id: str
    task_type: AgentTaskType
    enabled: bool = True
    depends_on: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowPlan(BaseModel):
    workflow_id: str
    session_id: str
    intent_type: str
    tasks: List[AgentTask] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RecommendationItem(BaseModel):
    recommendation_id: str
    title: str
    action: str
    rationale: str
    evidence_refs: List[str] = Field(default_factory=list)
    expected_metric: Optional[str] = None
    validation_checkpoint: Optional[str] = None
    status: Literal["pending", "in_progress", "done"] = "pending"
    outcome: Optional[Literal["improved", "no_change", "worse"]] = None
    score: float = 0.0


class WorkflowArtifacts(BaseModel):
    session_context: ReviewSessionContext
    intent: Optional[ReviewIntent] = None
    data_context: Optional[ReviewDataContext] = None
    knowledge_graph_context: str = ""
    relevant_chunks: List[Any] = Field(default_factory=list)
    structured_context: str = ""
    risk_context: str = ""
    kb_context: str = ""
    recommendations: List[RecommendationItem] = Field(default_factory=list)
    response_text: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowTrace(BaseModel):
    workflow_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: Literal["running", "completed", "failed"] = "running"
    steps: List[Dict[str, Any]] = Field(default_factory=list)

