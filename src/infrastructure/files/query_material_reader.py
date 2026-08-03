from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import SourceRecord
from src.infrastructure.files.extractor import extract_document_bytes

_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class VerifiedFragment:
    locator: str
    text: str
    fragment_id: str | None = None


@dataclass(frozen=True)
class VerifiedQueryMaterial:
    source_id: str
    filename: str
    document_version: str
    sha256: str
    text: str
    fragments: tuple[VerifiedFragment, ...]
    authority_level: AuthorityLevel
    security_level: SecurityLevel
    is_baseline_asset: bool


class LocalQueryMaterialReader:
    """Verify and read only trusted local material eligible for query evidence."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def read_baseline(
        self,
        *,
        project_id: str,
        asset_id: str,
        version: str,
        relative_path: str,
        expected_sha256: str,
    ) -> VerifiedQueryMaterial:
        expected = Path("data/obsidian_vault/02_Current_Baseline") / version / "full.md"
        candidate = Path(relative_path)
        if (
            not project_id.strip()
            or not asset_id.strip()
            or Path(version).name != version
            or candidate != expected
        ):
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "QUERY_BASELINE_ASSET_PATH_INVALID",
            )
        path = (self.project_root / candidate).resolve()
        if not path.is_relative_to(self.project_root) or not path.is_file():
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "QUERY_BASELINE_ASSET_PATH_INVALID",
            )
        try:
            payload = path.read_bytes()
            actual_sha256 = hashlib.sha256(payload).hexdigest()
            text = payload.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "QUERY_BASELINE_ASSET_UNREADABLE",
            ) from error
        if actual_sha256 != expected_sha256:
            raise DomainError(
                ErrorCode.BASELINE_INTEGRITY_FAILED,
                "QUERY_BASELINE_ASSET_HASH_MISMATCH",
            )
        return VerifiedQueryMaterial(
            source_id=asset_id,
            filename=path.name,
            document_version=version,
            sha256=actual_sha256,
            text=text,
            fragments=_markdown_fragments(text),
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            security_level=SecurityLevel.L2_INTERNAL,
            is_baseline_asset=True,
        )

    def read_source(self, source: SourceRecord) -> VerifiedQueryMaterial:
        path = Path(source.archive_path).resolve()
        expected_root = (
            self.project_root / "data/source_archive" / source.project_id / source.id
        ).resolve()
        if (
            path.parent != expected_root
            or path.name != source.original_filename
            or not path.is_file()
        ):
            raise DomainError(ErrorCode.CITATION_INVALID, "QUERY_SOURCE_ARCHIVE_PATH_INVALID")
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise DomainError(
                ErrorCode.CITATION_INVALID,
                "QUERY_SOURCE_ARCHIVE_UNREADABLE",
            ) from error
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != source.sha256 or len(payload) != source.size_bytes:
            raise DomainError(ErrorCode.CITATION_INVALID, "QUERY_SOURCE_ARCHIVE_HASH_MISMATCH")
        extracted = extract_document_bytes(
            payload,
            filename=path.name,
            source_id=source.id,
        )
        return VerifiedQueryMaterial(
            source_id=source.id,
            filename=path.name,
            document_version=source.document_version,
            sha256=actual_sha256,
            text=extracted.text,
            fragments=tuple(
                VerifiedFragment(
                    locator=chunk.locator,
                    text=chunk.text,
                    fragment_id=chunk.chunk_id,
                )
                for chunk in extracted.chunks
            ),
            authority_level=source.authority_level,
            security_level=source.security_level,
            is_baseline_asset=False,
        )

    @staticmethod
    def total_chars(materials: list[VerifiedQueryMaterial]) -> int:
        return sum({material.sha256: len(material.text) for material in materials}.values())


def _markdown_fragments(text: str) -> tuple[VerifiedFragment, ...]:
    headings: dict[int, str] = {}
    fragments: list[VerifiedFragment] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _MARKDOWN_HEADING.match(line)
        if match:
            level = len(match.group(1))
            headings[level] = match.group(2)
            for deeper in tuple(headings):
                if deeper > level:
                    del headings[deeper]
        if not line:
            continue
        heading_path = " > ".join(headings[level] for level in sorted(headings))
        locator = f"line:{line_number}"
        if heading_path:
            locator = f"heading:{heading_path}; {locator}"
        fragments.append(VerifiedFragment(locator=locator, text=line))
    return tuple(fragments)
