import logging
import time

from sqlmodel import Session

from app.core.db import engine
from app.file_storage import default_file_storage
from app.models import Upload
from app.models.document import DocumentMetadata
from app.utils.uuid6 import uuid7
from app.types import MimeTypes

logger = logging.getLogger(__name__)

def save_crm_to_file(crm_data_type, data, doc_content, doc_datetime, doc_metadata):
    try:
        file_name = f"{getattr(data, f'{crm_data_type}_name','未具名')}_{getattr(data, 'unique_id')}.md"
        file_path = f"crm/{crm_data_type}/{int(time.time())}-{uuid7().hex}.md"
        
        default_file_storage.save(file_path, doc_content)
        with Session(engine) as session:
            upload = Upload(
                name=file_name,
                size=default_file_storage.size(file_path),
                path=file_path,
                mime_type=MimeTypes.MARKDOWN,
                created_at=doc_datetime,
                updated_at=doc_datetime
            )
            document_metadata = DocumentMetadata(
                **doc_metadata
            )
            upload.set_metadata(document_metadata)
            session.add(upload)
            session.commit()
            session.refresh(upload)
            logger.info(f"Successfully saved CRM file: {file_name} as {upload.path}")
            return upload
    except Exception as e:
        logger.error(f"Failed to save CRM file: {e}")
        return None