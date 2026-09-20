from dataclasses import dataclass
from typing import Literal, Optional


MatchType = Literal["exact", "suggested"]
MatchStatus = Literal["matched", "ambiguous", "suggested", "unmatched", "linked"]


@dataclass(frozen=True)
class UploadRef:
    id: int
    name: str


@dataclass(frozen=True)
class DocumentRef:
    id: int
    name: str
    knowledge_base_id: Optional[int] = None
    file_id: Optional[int] = None


@dataclass
class MatchCandidate:
    document_id: int
    document_name: str
    knowledge_base_id: Optional[int]
    match_type: MatchType
    already_linked: bool = False
    linked_upload_id: Optional[int] = None


@dataclass
class UploadMatchPreview:
    upload_id: int
    upload_name: str
    status: MatchStatus
    candidates: list[MatchCandidate]
    linked_document_id: Optional[int] = None


def filename_stem(name: str) -> str:
    """Strip a single trailing extension from a file name."""
    basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    if not basename:
        return ""
    if basename.startswith(".") and basename.count(".") == 1:
        return basename
    if "." not in basename:
        return basename
    stem = basename.rsplit(".", 1)[0]
    return stem if stem else basename


def names_match(upload_name: str, document_name: str) -> bool:
    """True when names are equal or share the same stem (extension ignored)."""
    if upload_name.casefold() == document_name.casefold():
        return True
    upload_stem = filename_stem(upload_name).casefold()
    document_stem = filename_stem(document_name).casefold()
    return bool(upload_stem and document_stem and upload_stem == document_stem)


def preview_upload_matches(
    uploads: list[UploadRef],
    documents: list[DocumentRef],
    linked_document_to_upload: dict[int, int] | None = None,
    linked_upload_to_document: dict[int, int] | None = None,
) -> list[UploadMatchPreview]:
    """Classify exact vs stem (suggested) matches without writing anything."""
    linked_document_to_upload = linked_document_to_upload or {}
    linked_upload_to_document = linked_upload_to_document or {}
    results: list[UploadMatchPreview] = []

    for upload in uploads:
        upload_key = upload.name.casefold()
        upload_stem = filename_stem(upload.name).casefold()
        exact: list[MatchCandidate] = []
        suggested: list[MatchCandidate] = []

        for document in documents:
            if document.file_id == upload.id:
                continue

            document_key = document.name.casefold()
            document_stem = filename_stem(document.name).casefold()
            linked_upload_id = linked_document_to_upload.get(document.id)

            if document_key == upload_key:
                match_type: MatchType = "exact"
            elif upload_stem and document_stem and document_stem == upload_stem:
                match_type = "suggested"
            else:
                continue

            candidate = MatchCandidate(
                document_id=document.id,
                document_name=document.name,
                knowledge_base_id=document.knowledge_base_id,
                match_type=match_type,
                already_linked=linked_upload_id is not None,
                linked_upload_id=linked_upload_id,
            )
            if match_type == "exact":
                exact.append(candidate)
            else:
                suggested.append(candidate)

        linked_document_id = linked_upload_to_document.get(upload.id)
        if linked_document_id is not None:
            status: MatchStatus = "linked"
        elif len(exact) == 1:
            status = "matched"
        elif len(exact) > 1:
            status = "ambiguous"
        elif suggested:
            status = "suggested"
        else:
            status = "unmatched"

        results.append(
            UploadMatchPreview(
                upload_id=upload.id,
                upload_name=upload.name,
                status=status,
                candidates=exact + suggested,
                linked_document_id=linked_document_id,
            )
        )

    return results
