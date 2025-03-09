import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import SessionDep, CurrentSuperuserDep
from app.utils import tos
from app.core.config import settings

router = APIRouter()

logger = logging.getLogger(__name__)

class STSCredentialResponse(BaseModel):
    access_key_id: str
    secret_access_key: str
    session_token: str
    
@router.get("/tos/sts", response_model=STSCredentialResponse)
def get_tos_sts():
    try:
        text = tos.get_sts_token(
            host=settings.TOS_API_HOST,
            region =  settings.TOS_API_REGION,
            access_key = settings.TOS_API_KEY,
            secret_key = settings.TOS_API_SECRET
        )
        
        logger.info(f"TOS STS: {text}")
        credentials = json.loads(text)["Result"]["Credentials"]
        
        return STSCredentialResponse(
            access_key_id=credentials["AccessKeyId"],
            secret_access_key=credentials["SecretAccessKey"],
            session_token=credentials["SessionToken"]
        )
    except Exception as e:
        logger.error(f"Failed to get TOS STS: {e}")
        raise HTTPException(status_code=500, detail="Failed to get TOS STS")
