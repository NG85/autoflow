from http import HTTPStatus
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination import Page, Params
from pydantic import BaseModel
from sqlmodel import and_, select

from app.api.deps import CurrentUserDep, SessionDep
from app.core.config import settings, StorageType
from app.utils import sts
from app.api.routes.models import NotifyTosUploadRequest
from app.models.data_source import DataSource
from app.models.upload import Upload
from app.types import MimeTypes

logger = logging.getLogger(__name__)

router = APIRouter()

class STSCredentialResponse(BaseModel):
    access_key_id: str
    secret_access_key: str
    session_token: str
    endpoint: str
    bucket: str
    path_prefix: str
    region: Optional[str] = None
    storage_type: StorageType

@router.get("/sts", response_model=STSCredentialResponse)
def get_sts(
    user: CurrentUserDep,
    access_key: Optional[str] = Query(None, description="Access key for TOS")
):
    try:
        storage_type = settings.STORAGE_TYPE
        
        if storage_type == StorageType.TOS:
            if access_key != settings.TOS_API_KEY:
                raise HTTPException(status_code=400, detail="This access key is not authorized to get STS token")
            
            text = sts.get_tos_sts_token(
                host=settings.TOS_API_HOST,
                region=settings.TOS_REGION,
                access_key=settings.TOS_API_KEY,
                secret_key=settings.TOS_API_SECRET
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
                path_prefix=settings.TOS_PATH_PREFIX,
                storage_type=storage_type
            )
        else:  # MinIO
            result = sts.get_minio_sts_token(
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                bucket=settings.MINIO_BUCKET,
                endpoint=settings.MINIO_ENDPOINT
            )
            
            credentials = result["Result"]["Credentials"]
            
            return STSCredentialResponse(
                access_key_id=credentials["access_key_id"],
                secret_access_key=credentials["secret_access_key"],
                session_token=credentials["session_token"],
                endpoint=settings.MINIO_ENDPOINT,
                bucket=settings.MINIO_BUCKET,
                path_prefix=settings.MINIO_PATH_PREFIX,
                storage_type=storage_type
            )
    except Exception as e:
        logger.error(f"Failed to get STS credentials: {e}")
        raise HTTPException(status_code=500, detail="Failed to get STS credentials")
    

@router.post("/notify-upload")
def notify_upload(
    session: SessionDep,
    user: CurrentUserDep,
    notify: NotifyTosUploadRequest,
) -> dict: 
    """
    Notify the system about files uploaded to storage (TOS or MinIO).
    This endpoint saves the uploaded file information to the database,
    creates a data source, but does not start the async indexing task.
    """
    try:
        logger.info(f"Received upload notification for {settings.STORAGE_TYPE}: {notify}")
        
        # Save uploaded files to db
        uploads = []
        for config in notify.config:
            # Create Upload object for each file
            upload = Upload(
                name=config.name,
                size=config.size,
                path=config.path,
                mime_type=MimeTypes(config.mime_type),
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
        
        logger.info(f"Created data source #{data_source.id} for {settings.STORAGE_TYPE} uploads")
        
        # Return data source id
        return {"data_source_id": data_source.id}
        
    except Exception as e:
        logger.error(f"Failed to process {settings.STORAGE_TYPE} upload notification: {e}")
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail="Failed to process upload notification")


@router.get("/uploads", response_model=Page[Upload])
def list_uploads(
    session: SessionDep,
    user: CurrentUserDep,
    category: Optional[str] = Query(None, description="Filter by file category (e.g., product, account, competitor)"),
    params: Params = Depends(),
) -> Page[Upload]:
    """
    List all files uploaded through storage (TOS or MinIO).
    
    Files uploaded through storage are marked with "source: customer-XXX" in their metadata,
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
        # Base query to get uploads with customer source
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
        
        return Page.create(
            items=results,
            total=total,
            params=params,
        )
        
    except Exception as e:
        logger.error(f"Failed to list {settings.STORAGE_TYPE} uploads: {e}")
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail="Failed to retrieve uploads")