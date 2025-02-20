from typing import List
from pydantic import BaseModel, Field

class QuestionAnalysisResult(BaseModel):
    """Question analysis result"""
    is_competitor_related: bool = Field(description="Whether the question is related to competitor")
    competitor_focus: str = Field(description="The main competitive aspect being discussed")
    competitor_names: List[str] = Field(default_factory=list, description="List of competitor names mentioned")
    comparison_aspects: List[str] = Field(default_factory=list, description="List of aspects being compared")
    needs_technical_details: bool = Field(default=False, description="Whether technical details are needed")
    
    class Config:
        """Pydantic model configuration"""
        arbitrary_types_allowed = True
