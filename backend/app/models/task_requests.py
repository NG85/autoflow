from typing import List, Optional
from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    subject: str = Field(..., description="Task subject")
    status: str = Field(..., description="Task status, e.g., Open/Completed")
    owner_id: Optional[str] = Field(None, description="Salesforce User Id for Owner; default current user if omitted")
    what_id: str = Field(..., description="Salesforce Id of related record (Account/Opportunity/etc.)")
    progress: str = Field(..., description="Progress notes; will be saved to Progress__c (max 255)")
    risk_and_next_step: str = Field(..., description="Risk and next step; saved to Risk_and_Next_Step__c (max 255)")
    priority: Optional[str] = Field(None, description="Task priority, e.g., Normal/High")

    # optional passthroughs
    who_id: Optional[str] = None
    activity_date: str = Field(..., description="Due date YYYY-MM-DD")
    description: Optional[str] = None
    reminder_datetime: Optional[str] = None  # 2024-01-15T09:00:00.000+0000


class TaskBatchCreateRequest(BaseModel):
    tasks: List[TaskCreateRequest]
    partial_fail: bool = True
