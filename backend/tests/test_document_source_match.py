from app.services.document_source_match import (
    DocumentRef,
    UploadRef,
    filename_stem,
    names_match,
    preview_upload_matches,
)


def test_filename_stem_strips_last_extension():
    assert filename_stem("报告.docx") == "报告"
    assert filename_stem("foo.bar.md") == "foo.bar"
    assert filename_stem("README") == "README"
    assert filename_stem(".env") == ".env"
    assert filename_stem("path/to/file.pptx") == "file"


def test_names_match_exact_and_stem():
    assert names_match("报告.docx", "报告.docx")
    assert names_match("报告.DOCX", "报告.docx")
    assert names_match("报告.docx", "报告.md")
    assert not names_match("报告.docx", "附录.md")


def test_preview_exact_match_is_ready_to_confirm():
    results = preview_upload_matches(
        uploads=[UploadRef(id=1, name="客户方案.docx")],
        documents=[
            DocumentRef(id=10, name="客户方案.docx", knowledge_base_id=2, file_id=99)
        ],
    )
    assert results[0].status == "matched"
    assert results[0].candidates[0].match_type == "exact"
    assert results[0].candidates[0].document_id == 10


def test_preview_stem_match_is_suggested():
    results = preview_upload_matches(
        uploads=[UploadRef(id=1, name="客户方案.docx")],
        documents=[
            DocumentRef(id=10, name="客户方案.md", knowledge_base_id=2, file_id=99)
        ],
    )
    assert results[0].status == "suggested"
    assert results[0].candidates[0].match_type == "suggested"


def test_preview_multiple_exact_matches_are_ambiguous():
    results = preview_upload_matches(
        uploads=[UploadRef(id=1, name="客户方案.docx")],
        documents=[
            DocumentRef(id=10, name="客户方案.docx", knowledge_base_id=2),
            DocumentRef(id=11, name="客户方案.docx", knowledge_base_id=3),
        ],
    )
    assert results[0].status == "ambiguous"
    assert [c.document_id for c in results[0].candidates] == [10, 11]


def test_preview_skips_document_that_already_uses_the_upload():
    results = preview_upload_matches(
        uploads=[UploadRef(id=1, name="客户方案.docx")],
        documents=[DocumentRef(id=10, name="客户方案.docx", file_id=1)],
    )
    assert results[0].status == "unmatched"
    assert results[0].candidates == []


def test_preview_marks_already_linked_upload():
    results = preview_upload_matches(
        uploads=[UploadRef(id=1, name="客户方案.docx")],
        documents=[DocumentRef(id=10, name="客户方案.docx")],
        linked_document_to_upload={10: 1},
        linked_upload_to_document={1: 10},
    )
    assert results[0].status == "linked"
    assert results[0].linked_document_id == 10
    assert results[0].candidates[0].already_linked is True
