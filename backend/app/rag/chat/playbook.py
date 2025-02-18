from dataclasses import dataclass
import logging

from typing import List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

@dataclass
class QuestionAnalysisResult(BaseModel):
    """Question analysis result"""
    is_sales_related: bool = Field(description="Whether the question is related to sales")
    enhanced_question: Optional[str] = Field(default=None, description="Enhanced question")
    related_aspects: List[str] = Field(default_factory=list, description="Related aspects to consider")
