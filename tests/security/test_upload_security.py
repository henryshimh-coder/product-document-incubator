from __future__ import annotations

import pytest

from src.domain.errors import DomainError, ErrorCode


def file_safety():
    """Loads the production boundary so missing behavior reports as a test failure."""
    import importlib

    return importlib.import_module("src.domain.services.file_safety")


@pytest.mark.parametrize(
    "filename",
    ["../../secret.md", "..\\..\\secret.md", "report.pdf.exe", "/tmp/report.md"],
)
def test_unsafe_filename_is_rejected(filename: str) -> None:
    """Catches traversal or disguised filenames reaching archive path construction."""
    with pytest.raises(DomainError, match="UNSAFE_FILENAME") as error:
        file_safety().sanitize_filename(filename)

    assert error.value.code == ErrorCode.FILE_TYPE_NOT_ALLOWED


def test_upload_rejects_payload_that_does_not_match_pdf_extension() -> None:
    """Catches accepting a text executable renamed with a PDF suffix."""
    with pytest.raises(DomainError, match="MIME_MISMATCH") as error:
        file_safety().validate_upload("risk.pdf", b"not a PDF")

    assert error.value.code == ErrorCode.FILE_TYPE_NOT_ALLOWED


def test_upload_rejects_files_larger_than_limit() -> None:
    """Catches archives accepting content beyond the configured 20 MiB boundary."""
    with pytest.raises(DomainError) as error:
        file_safety().validate_upload("risk.txt", b"a" * (20 * 1024 * 1024 + 1))

    assert error.value.code == ErrorCode.FILE_TOO_LARGE


def test_upload_accepts_utf8_markdown() -> None:
    """Catches security validation blocking a valid allowed text document."""
    filename = "风险意见.md"
    assert file_safety().validate_upload(filename, "# 风险意见\n内容".encode()) == filename


@pytest.mark.parametrize(
    "filename",
    [
        "产品方案 2.2（终稿）.md",
        "需求说明【待确认】.txt",
        "2026-08-23 产品文档（V1.0）.docx",
    ],
)
def test_upload_accepts_normal_owner_filenames_with_spaces_and_punctuation(filename: str) -> None:
    """Catches common macOS/Windows document names being mistaken for unsafe paths."""
    assert file_safety().sanitize_filename(filename) == filename
