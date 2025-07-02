import logging
from typing import List, Literal, Optional
from fastapi import APIRouter, Body, HTTPException
from app.api.deps import OptionalUserDep, SessionDep
from app.feishu.push_reports import push_weekly_reports
from app.feishu.push_review import push_account_review
from app.feishu.push_release_notes import push_release_notes

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/feishu"
)

@router.post("/push-release-notes")
def push_release_notes_api(
    db_session: SessionDep,
    user: OptionalUserDep,
    notes_type: Literal["text", "post"] = Body(..., example="text"),
    external: bool = Body(False, example=False),
    notes: str = Body(..., embed=True)):
    try:
        push_release_notes(db_session, notes, notes_type, external)
        return {"code": 200, "message": "ok"} 

    except Exception as e:
        logger.error(f"Failed to push release notes: {e}")
        raise HTTPException(status_code=500, detail="Failed to push release notes")
    

@router.post("/push-weekly-reports")
def push_weekly_reports_api(
    external: bool = Body(False, example=False),
    legacy: bool = Body(False, example=False),
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

        push_weekly_reports(items, receivers, external, legacy)
        return {"code": 200, "message": "ok"} 

    except Exception as e:
        logger.error(f"Failed to push weekly reports: {e}")
        raise HTTPException(status_code=500, detail="Failed to push weekly reports")
    

@router.post("/push-account-review")
def push_account_review_api(
    external: bool = Body(False, example=False),
    review_date: str = Body(..., example="2025-06-20"),
    items: List[dict] = Body(..., example=[
        {"execution_id": "1234567890", "account_id": "1234567890", "account_name": "测试公司"}
    ]),
    leaders: Optional[List[dict]] = Body(None, example=[
        {"name": "张三", "email": "zhangsan@aptsell.ai", "open_id": "ou_1234567890"}
    ]),
    sales: Optional[List[dict]] = Body(None, example=[
        {"name": "张三", "email": "zhangsan@aptsell.ai", "open_id": "ou_1234567890"}
    ])
):
    try:
        if not items:
            logger.info("No account review to push")
            return {"code": 200, "message": "ok"} 
        
        push_account_review(items, review_date, leaders, sales, external)
        return {"code": 200, "message": "ok"} 

    except Exception as e:
        logger.error(f"Failed to push account review: {e}")
        raise HTTPException(status_code=500, detail="Failed to push account review")