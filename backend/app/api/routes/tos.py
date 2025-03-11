from http import HTTPStatus
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination import Page, Params, paginate
from pydantic import BaseModel
from sqlmodel import and_, select

from app.api.deps import CurrentUserDep, SessionDep
from app.utils import tos
from app.core.config import settings
from app.api.routes.models import NotifyTosUploadRequest
from app.models.upload import Upload
from app.models.data_source import DataSource

router = APIRouter()

logger = logging.getLogger(__name__)

class STSCredentialResponse(BaseModel):
    access_key_id: str
    secret_access_key: str
    session_token: str
    endpoint: str
    region: str
    bucket: str
    path_prefix: str

    
@router.get("/tos/sts", response_model=STSCredentialResponse)
def get_tos_sts(user: CurrentUserDep, access_key: str):
    if access_key != settings.TOS_API_KEY:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="This access key is not authorized to get STS token")
    try:
        text = tos.get_sts_token(
            host=settings.TOS_API_HOST,
            region =  settings.TOS_REGION,
            access_key = settings.TOS_API_KEY,
            secret_key = settings.TOS_API_SECRET
        )
        
        logger.info(f"TOS STS: {text}")
        credentials = json.loads(text)["Result"]["Credentials"]
        
        return STSCredentialResponse(
            access_key_id=credentials["AccessKeyId"],
            secret_access_key=credentials["SecretAccessKey"],
            session_token=credentials["SessionToken"],
            endpoint=settings.TOS_ENDPOINT,
            region=settings.TOS_REGION,
            bucket=settings.TOS_BUCKET,
            path_prefix=settings.TOS_PATH_PREFIX
        )
    except Exception as e:
        logger.error(f"Failed to get TOS STS: {e}")
        raise HTTPException(status_code=500, detail="Failed to get TOS STS")


@router.post("/tos/notify-upload")
def notify_tos_upload(
    session: SessionDep,
    user: CurrentUserDep,
    notify: NotifyTosUploadRequest,
) -> dict: 
    """
    Notify the system about files uploaded to TOS.
    This endpoint saves the uploaded file information to the database,
    creates a data source, but does not start the async indexing task.
    """
    try:
        logger.info(f"Received TOS upload notification: {notify}")
        
        # Save uploaded files to db
        uploads = []
        for config in notify.config:
            # Create Upload object for each file
            upload = Upload(
                name=config.name,
                size=config.size,
                path=config.path,
                mime_type=config.mime_type,
                user_id=user.id,
                meta=notify.meta
            )
            uploads.append(upload)
        
        session.add_all(uploads)
        session.commit()
        
        # Get the upload IDs after commit
        file_configs = [{"file_id": upload.id, "file_name": upload.name} for upload in uploads]
 
        # Create data source
        data_source = DataSource(
            name=notify.name,
            description="",
            user_id=user.id,
            data_source_type=notify.data_source_type,
            config=file_configs
        )
        
        session.add(data_source)
        session.commit()
        session.refresh(data_source)
        
        logger.info(f"Created data source #{data_source.id} for TOS uploads")
        
        # Return data source id
        return {"data_source_id": data_source.id}
        
    except Exception as e:
        logger.error(f"Failed to process TOS upload notification: {e}")
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail="Failed to process upload notification")


@router.get("/tos/uploads", response_model=Page[Upload])
def list_tos_uploads(
    session: SessionDep,
    user: CurrentUserDep,
    category: Optional[str] = Query(None, description="Filter by file category (e.g., product, account, competitor)"),
    params: Params = Depends(),
) -> Page[Upload]:
    """
    List all files uploaded through TOS.
    
    Files uploaded through TOS are marked with "source: customer-XXX" in their metadata,
    where XXX represents the file category (e.g., product, account, competitor).
    
    Args:
        session: Database session
        user: Current authenticated user
        category: Optional filter for specific file category
        params: Pagination parameters
        
    Returns:
        Paginated list of Upload objects
    """
    try:
        # Base query to get uploads with TOS source
        query = select(Upload).where(
            and_(
                Upload.user_id == user.id,
                Upload.meta["source"].as_string().startswith("customer")
            )
        )
                
        # Add category filter if provided
        if category:
            specific_source = f"customer-{category}"
            query = query.where(Upload.meta["source"].as_string() == specific_source)
        
        # Order by creation date, newest first
        query = query.order_by(Upload.created_at.desc())
        
        # Use offset and limit to implement database-level pagination, not loading all results
        total_count = session.exec(select(Upload.id).where(query.whereclause)).all()
        total = len(total_count)
        
        offset = (params.page - 1) * params.size
        limit = params.size        
        query = query.offset(offset).limit(limit)
        
        results = session.exec(query).all()
        
        return Page(
            items=results,
            total=total,
            page=params.page,
            size=params.size
        )
        
    except Exception as e:
        logger.error(f"Failed to list uploads: {e}")
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail="Failed to retrieve uploads")