import logging
from typing import List, Literal, Optional
from fastapi import APIRouter, Body, HTTPException
from app.api.deps import SessionDep
from app.feishu.push_reports import push_weekly_reports

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/feishu"
)

@router.post("/push-weekly-reports", deprecated=True)
def push_weekly_reports_api(
    db_session: SessionDep,
    report_type: Literal["review1", "review5"] = Body(..., example="review1"),
    external: bool = Body(False, example=False),
    platform: Optional[Literal["feishu", "lark"]] = Body(None, example="feishu"),
    items: List[dict] = Body(..., example=[
        {"execution_id": "1234567890", "report_name": "业绩及变化统计表-[2025-06-01 - 2025-06-06]"}
    ]),
    receivers: Optional[List[dict]] = Body(None, example=[
        {"name": "张三", "email": "zhangsan@aptsell.ai", "open_id": "ou_1234567890"}
    ])
):    
    try:
        if not items:
            logger.info("No weekly reports to push")
            return {"code": 200, "message": "ok"} 

        push_weekly_reports(items, receivers, report_type, external, platform, db_session)
        return {"code": 200, "message": "ok"} 

    except Exception as e:
        logger.error(f"Failed to push weekly reports: {e}")
        raise HTTPException(status_code=500, detail="Failed to push weekly reports")