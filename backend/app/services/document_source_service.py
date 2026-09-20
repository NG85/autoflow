from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.exceptions import DocumentNotFound
from app.models.document import Document
from app.models.document_source_file import DocumentSourceFile
from app.models.upload import Upload
from app.repositories.document_source_file import document_source_file_repo
from app.repositories.knowledge_base import knowledge_base_repo
from app.services.document_source_match import (
    UploadMatchPreview,
    UploadRef,
    names_match,
    preview_upload_matches,
)


class DocumentSourceLinkConflict(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=409, detail=detail)


def _uploads_for_user(
    session: Session, user_id: UUID, upload_ids: list[int]
) -> list[Upload]:
    uploads = list(
        session.exec(select(Upload).where(Upload.id.in_(upload_ids))).all()
    )
    found_ids = {upload.id for upload in uploads}
    missing = [upload_id for upload_id in upload_ids if upload_id not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"uploads not found: {missing}")
    forbidden = [upload.id for upload in uploads if upload.user_id != user_id]
    if forbidden:
        raise HTTPException(
            status_code=403, detail=f"no permission for uploads: {forbidden}"
        )
    by_id = {upload.id: upload for upload in uploads}
    return [by_id[upload_id] for upload_id in upload_ids]


def _link_maps(session: Session, upload_ids: list[int], document_ids: list[int]):
    links = document_source_file_repo.fetch_links(
        session, document_ids=document_ids, upload_ids=upload_ids
    )
    linked_document_to_upload = {
        link.document_id: link.original_upload_id for link in links
    }
    linked_upload_to_document = {
        link.original_upload_id: link.document_id for link in links
    }
    return linked_document_to_upload, linked_upload_to_document


def preview_matches(
    session: Session,
    user_id: UUID,
    upload_ids: list[int],
    knowledge_base_id: int | None = None,
) -> list[UploadMatchPreview]:
    if knowledge_base_id is not None:
        knowledge_base_repo.must_get(session, knowledge_base_id)

    uploads = _uploads_for_user(session, user_id, upload_ids)
    documents = document_source_file_repo.fetch_candidate_documents(
        session,
        upload_names=[upload.name for upload in uploads],
        knowledge_base_id=knowledge_base_id,
    )
    linked_document_to_upload, linked_upload_to_document = _link_maps(
        session,
        upload_ids=upload_ids,
        document_ids=[document.id for document in documents],
    )
    return preview_upload_matches(
        uploads=[UploadRef(id=upload.id, name=upload.name) for upload in uploads],
        documents=documents,
        linked_document_to_upload=linked_document_to_upload,
        linked_upload_to_document=linked_upload_to_document,
    )


def confirm_matches(
    session: Session,
    user_id: UUID,
    links: list[tuple[int, int]],
) -> list[tuple[DocumentSourceFile, Document, Upload]]:
    upload_ids = [upload_id for upload_id, _ in links]
    document_ids = [document_id for _, document_id in links]
    uploads_by_id = {
        upload.id: upload for upload in _uploads_for_user(session, user_id, upload_ids)
    }
    documents = {
        document.id: document
        for document in session.exec(
            select(Document).where(Document.id.in_(document_ids))
        ).all()
    }

    linked_document_to_upload, linked_upload_to_document = _link_maps(
        session, upload_ids=upload_ids, document_ids=document_ids
    )

    for upload_id, document_id in links:
        upload = uploads_by_id[upload_id]
        document = documents.get(document_id)
        if document is None:
            raise DocumentNotFound(document_id)
        if document.file_id == upload.id:
            raise HTTPException(
                status_code=400,
                detail=f"upload #{upload_id} is already the indexed file of document #{document_id}",
            )
        if not names_match(upload.name, document.name):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"upload '{upload.name}' does not match document '{document.name}'"
                ),
            )

        existing_document_id = linked_upload_to_document.get(upload_id)
        existing_upload_id = linked_document_to_upload.get(document_id)
        if existing_document_id == document_id and existing_upload_id == upload_id:
            continue
        if existing_document_id is not None:
            raise DocumentSourceLinkConflict(
                f"upload #{upload_id} is already linked to document #{existing_document_id}"
            )
        if existing_upload_id is not None:
            raise DocumentSourceLinkConflict(
                f"document #{document_id} is already linked to upload #{existing_upload_id}"
            )

        document_source_file_repo.create_link(
            session,
            document_id=document_id,
            original_upload_id=upload_id,
            confirmed_by=user_id,
            commit=False,
        )
        linked_upload_to_document[upload_id] = document_id
        linked_document_to_upload[document_id] = upload_id

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DocumentSourceLinkConflict(
            "document or original upload is already linked"
        ) from exc

    persisted = document_source_file_repo.fetch_links(
        session, document_ids=document_ids, upload_ids=upload_ids
    )
    link_by_pair = {
        (link.document_id, link.original_upload_id): link for link in persisted
    }
    return [
        (
            link_by_pair[(document_id, upload_id)],
            documents[document_id],
            uploads_by_id[upload_id],
        )
        for upload_id, document_id in links
    ]


def list_matches(
    session: Session,
    user_id: UUID,
    knowledge_base_id: int | None = None,
) -> list[tuple[DocumentSourceFile, Document, Upload]]:
    stmt = (
        select(DocumentSourceFile, Document, Upload)
        .join(Document, Document.id == DocumentSourceFile.document_id)
        .join(Upload, Upload.id == DocumentSourceFile.original_upload_id)
        .where(Upload.user_id == user_id)
        .order_by(DocumentSourceFile.created_at.desc())
    )
    if knowledge_base_id is not None:
        stmt = stmt.where(Document.knowledge_base_id == knowledge_base_id)
    return list(session.exec(stmt).all())


def unbind_match(session: Session, user_id: UUID, document_id: int) -> None:
    link = document_source_file_repo.get_by_document_id(session, document_id)
    if not link:
        raise HTTPException(status_code=404, detail="source file link not found")
    upload = session.get(Upload, link.original_upload_id)
    if upload is None or upload.user_id != user_id:
        raise HTTPException(status_code=403, detail="no permission to unbind this link")
    session.delete(link)
    session.commit()
