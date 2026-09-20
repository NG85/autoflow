from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.deps import SessionDep
from app.core.config import settings
from app.file_storage import get_file_storage
from app.models.upload import Upload
from app.repositories import document_repo
from app.repositories.document_source_file import document_source_file_repo

router = APIRouter()


def _candidate_storage_paths(path: str) -> list[str]:
    paths = [path]
    if path.startswith(settings.STORAGE_PATH_PREFIX):
        stripped = path.replace(settings.STORAGE_TENANT + "/", "", 1).lstrip("/")
        if stripped and stripped not in paths:
            paths.append(stripped)
    return paths


def _existing_storage_path(filestorage, path: str) -> str | None:
    for candidate in _candidate_storage_paths(path):
        if filestorage.exists(candidate):
            return candidate
    return None


@router.get("/documents/{doc_id}/download")
def download_file(
    doc_id: int,
    session: SessionDep,
    kind: str | None = Query(
        None, description="Use 'processed' to download the indexed file instead of the original"
    ),
):
    doc = document_repo.must_get(session, doc_id)
    filestorage = get_file_storage()

    download_name = doc.name
    media_type = doc.mime_type
    storage_path = None

    if kind != "processed":
        originals = document_source_file_repo.fetch_uploads_by_document_ids(
            session, [doc.id]
        )
        original: Upload | None = originals.get(doc.id)
        if original:
            storage_path = _existing_storage_path(filestorage, original.path)
            if storage_path:
                download_name = original.name
                media_type = original.mime_type

    if storage_path is None:
        storage_path = _existing_storage_path(filestorage, doc.source_uri)

    if storage_path is None:
        raise HTTPException(status_code=404, detail="File not found")

    file_size = filestorage.size(storage_path)
    headers = {
        "Content-Length": str(file_size),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}",
    }

    def iterfile():
        with filestorage.open(storage_path) as f:
            yield from f

    return StreamingResponse(iterfile(), media_type=str(media_type), headers=headers)
