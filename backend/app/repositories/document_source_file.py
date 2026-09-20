from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.models.document import Document
from app.models.document_source_file import DocumentSourceFile
from app.models.upload import Upload
from app.repositories.base_repo import BaseRepo
from app.services.document_source_match import DocumentRef, filename_stem


def _document_name_stem_expr():
    extension = func.substring_index(Document.name, ".", -1)
    return func.ifnull(
        func.nullif(
            func.left(
                Document.name,
                func.char_length(Document.name) - func.char_length(extension) - 1,
            ),
            "",
        ),
        Document.name,
    )


class DocumentSourceFileRepo(BaseRepo):
    model_cls = DocumentSourceFile

    def get_by_document_id(
        self, session: Session, document_id: int
    ) -> Optional[DocumentSourceFile]:
        stmt = select(DocumentSourceFile).where(
            DocumentSourceFile.document_id == document_id
        )
        return session.exec(stmt).first()

    def get_by_original_upload_id(
        self, session: Session, original_upload_id: int
    ) -> Optional[DocumentSourceFile]:
        stmt = select(DocumentSourceFile).where(
            DocumentSourceFile.original_upload_id == original_upload_id
        )
        return session.exec(stmt).first()

    def fetch_links(
        self,
        session: Session,
        document_ids: list[int] | None = None,
        upload_ids: list[int] | None = None,
    ) -> list[DocumentSourceFile]:
        if not document_ids and not upload_ids:
            return []
        conditions = []
        if document_ids:
            conditions.append(DocumentSourceFile.document_id.in_(document_ids))
        if upload_ids:
            conditions.append(DocumentSourceFile.original_upload_id.in_(upload_ids))
        stmt = select(DocumentSourceFile).where(or_(*conditions))
        return list(session.exec(stmt).all())

    def fetch_uploads_by_document_ids(
        self, session: Session, document_ids: list[int]
    ) -> dict[int, Upload]:
        if not document_ids:
            return {}
        stmt = (
            select(DocumentSourceFile.document_id, Upload)
            .join(Upload, Upload.id == DocumentSourceFile.original_upload_id)
            .where(DocumentSourceFile.document_id.in_(document_ids))
        )
        return {document_id: upload for document_id, upload in session.exec(stmt).all()}

    def fetch_candidate_documents(
        self,
        session: Session,
        upload_names: list[str],
        knowledge_base_id: Optional[int] = None,
    ) -> list[DocumentRef]:
        if not upload_names:
            return []

        exact_names = list({name.lower() for name in upload_names if name})
        stems = list(
            {
                filename_stem(name).lower()
                for name in upload_names
                if filename_stem(name)
            }
        )
        stem_expr = _document_name_stem_expr()
        name_filters = []
        if exact_names:
            name_filters.append(func.lower(Document.name).in_(exact_names))
        if stems:
            name_filters.append(func.lower(stem_expr).in_(stems))
        if not name_filters:
            return []

        stmt = select(
            Document.id,
            Document.name,
            Document.knowledge_base_id,
            Document.file_id,
        ).where(or_(*name_filters))
        if knowledge_base_id is not None:
            stmt = stmt.where(Document.knowledge_base_id == knowledge_base_id)

        return [
            DocumentRef(
                id=row[0],
                name=row[1],
                knowledge_base_id=row[2],
                file_id=row[3],
            )
            for row in session.exec(stmt).all()
        ]

    def create_link(
        self,
        session: Session,
        document_id: int,
        original_upload_id: int,
        confirmed_by: UUID,
        commit: bool = True,
    ) -> DocumentSourceFile:
        link = DocumentSourceFile(
            document_id=document_id,
            original_upload_id=original_upload_id,
            confirmed_by=confirmed_by,
        )
        session.add(link)
        if commit:
            session.commit()
            session.refresh(link)
        return link

    def delete_by_document_id(self, session: Session, document_id: int) -> bool:
        link = self.get_by_document_id(session, document_id)
        if not link:
            return False
        session.delete(link)
        session.commit()
        return True


document_source_file_repo = DocumentSourceFileRepo()
