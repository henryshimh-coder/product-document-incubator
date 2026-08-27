from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path

from src.domain.errors import DomainError, ErrorCode

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_SOURCE_ARCHIVE_ROOT = Path("data/source_archive")
_FORBIDDEN_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_BUSINESS_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_business_id(value: str, field_name: str) -> str:
    """Validate a path-safe business identifier used by archive adapters."""
    if not _SAFE_BUSINESS_ID.fullmatch(value):
        raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, detail=f"UNSAFE_{field_name.upper()}")
    return value


def resolve_source_archive_root() -> Path:
    """Resolve the fixed repository-relative source archive root."""
    return DEFAULT_SOURCE_ARCHIVE_ROOT.resolve()


def sanitize_filename(filename: str) -> str:
    """Return an archive-safe filename while allowing ordinary document names."""
    if (
        not filename
        or not filename.strip()
        or filename in {".", ".."}
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or _FORBIDDEN_FILENAME_CHARS.search(filename) is not None
        or Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS
    ):
        raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, detail="UNSAFE_FILENAME")
    return filename


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(content)) as document:
            names = set(document.namelist())
    except zipfile.BadZipFile:
        return False
    return (
        "[Content_Types].xml" in names
        and "word/document.xml" in names
        and not any(name.lower().endswith("vbaproject.bin") for name in names)
    )


def detect_mime_type(content: bytes) -> str | None:
    """Identify only the permitted document formats from their actual content."""
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if _is_docx(content):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
    return "text/plain"


def validate_upload(
    filename: str,
    content: bytes,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> str:
    """Validate a user file before it reaches the immutable local archive."""
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, detail="UNSAFE_FILENAME")
    if not content:
        raise DomainError(ErrorCode.FILE_TOO_LARGE, detail="EMPTY_FILE")
    if len(content) > max_bytes:
        raise DomainError(ErrorCode.FILE_TOO_LARGE)

    mime_type = detect_mime_type(content)
    expected_mime = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
        ".md": "text/plain",
    }[suffix]
    if mime_type != expected_mime:
        raise DomainError(ErrorCode.FILE_TYPE_NOT_ALLOWED, detail="MIME_MISMATCH")
    return safe_name
