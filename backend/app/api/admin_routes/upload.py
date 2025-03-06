import json
import logging
import os
import time
from typing import List, Optional
from fastapi import APIRouter, Form, UploadFile, HTTPException, status

from app.api.deps import SessionDep, CurrentSuperuserDep
from app.file_storage import default_file_storage
from app.utils.uuid6 import uuid7
from app.models import Upload, DocumentCategory, DocumentMetadata
from app.types import MimeTypes
from app.site_settings import SiteSetting
from typing import List, Optional

router = APIRouter()


SUPPORTED_FILE_TYPES = {
    ".txt": MimeTypes.PLAIN_TXT,
    ".md": MimeTypes.MARKDOWN,
    ".pdf": MimeTypes.PDF,
    ".docx": MimeTypes.DOCX,
    ".pptx": MimeTypes.PPTX,
    ".xlsx": MimeTypes.XLSX,
    ".csv": MimeTypes.CSV,
}


logger = logging.getLogger(__name__)

@router.post("/admin/uploads")
def upload_files(
    session: SessionDep, user: CurrentSuperuserDep, files: List[UploadFile], meta: Optional[str] = Form(None)
) -> List[Upload]:
    """Upload files with metadata.

    For competitor documents, the required metadata format is:
    ```json
    {
        "doc_owner": "competitor",
        "product_name": "MongoDB Atlas",     # Required: Name of the competitor product
        "company_name": "MongoDB Inc.",      # Required: Name of the company
        "product_category": "Cloud Database" # Required: Product category
    }
    ```
    """
    
    uploads = []
    metadata_dict = json.loads(meta) if meta else {}
    
    # Check required fields for competitor documents
    if metadata_dict.get("doc_owner") == "competitor":
        required_fields = ["product_name", "company_name", "product_category"]
        missing_fields = [field for field in required_fields if not metadata_dict.get(field)]
        if missing_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required fields for competitor document: {', '.join(missing_fields)}"
            )
    
    # Create DocumentMetadata object
    document_metadata = DocumentMetadata(
        category=metadata_dict.pop("category", DocumentCategory.GENERAL),
        **metadata_dict
    )
    
    logger.info(f"document_metadata: {document_metadata}")
    for file in files:
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File name cannot be empty",
            )
        sys_max_upload_file_size = SiteSetting.max_upload_file_size
        if file.size > sys_max_upload_file_size:
            upload_file_size_in_mb = file.size / 1024 / 1024
            max_upload_file_size_in_mb = sys_max_upload_file_size / 1024 / 1024
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="The upload file size ({:.2f} MiB) exceeds maximum allowed size ({:.2f} MiB)".format(
                    upload_file_size_in_mb, max_upload_file_size_in_mb
                ),
            )

        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in SUPPORTED_FILE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File type {file_ext} not supported. Supported types: {SUPPORTED_FILE_TYPES.keys()}",
            )
        file_path = f"uploads/{user.id.hex}/{int(time.time())}-{uuid7().hex}{file_ext}"
        default_file_storage.save(file_path, file.file)
        upload = Upload(
            name=file.filename,
            size=default_file_storage.size(file_path),
            path=file_path,
            mime_type=SUPPORTED_FILE_TYPES[file_ext],
            user_id=user.id,
        )
        upload.set_metadata(document_metadata)
        uploads.append(upload)
        
    session.add_all(uploads)
    session.commit()
    return uploads
