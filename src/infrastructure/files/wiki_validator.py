from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.domain.errors import DomainError, ErrorCode
from src.domain.wiki import WikiChangeSet
from src.infrastructure.files.project_library import ProjectPaths

_FRONTMATTER_REQUIRED_FIELDS = {
    "project_id",
    "source_id",
    "material_series_id",
    "material_version",
    "raw_path",
    "raw_sha256",
    "source_type",
    "authority_level",
    "security_level",
    "schema_version",
    "generation_mode",
    "ingested_at",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OBSIDIAN_LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


class WikiValidator:
    """Reject unsafe or internally inconsistent Wiki change sets before commit."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths

    def validate_change_set(self, change_set: WikiChangeSet) -> None:
        try:
            change_set.validate_contract()
            self._validate_project(change_set)
            changed_paths = {change.relative_path for change in change_set.page_changes}
            for change in change_set.page_changes:
                self._validate_project_path(change.relative_path)
                self._validate_links(change.markdown, changed_paths)
            source_change = next(
                change
                for change in change_set.page_changes
                if change.relative_path == change_set.source_page_path
            )
            self._validate_source_frontmatter(source_change.markdown, change_set)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            raise DomainError(ErrorCode.WIKI_CHANGESET_INVALID, str(error)) from error

    def _validate_project(self, change_set: WikiChangeSet) -> None:
        if change_set.project_id != self.paths.project_id:
            raise ValueError("project_id does not match resolved project root")

    def _validate_project_path(self, relative_path: str) -> None:
        target = (self.paths.project_root / relative_path).resolve()
        if not target.is_relative_to(self.paths.project_root):
            raise ValueError("target resolves outside project root")

    def _validate_source_frontmatter(self, markdown: str, change_set: WikiChangeSet) -> None:
        frontmatter = self._parse_frontmatter(markdown)
        missing_fields = _FRONTMATTER_REQUIRED_FIELDS - set(frontmatter)
        if missing_fields:
            raise ValueError(f"source frontmatter missing: {sorted(missing_fields)}")
        if any(
            not isinstance(frontmatter[field], str) or not frontmatter[field].strip()
            for field in _FRONTMATTER_REQUIRED_FIELDS
        ):
            raise ValueError("source frontmatter has blank required values")
        if frontmatter["project_id"] != self.paths.project_id:
            raise ValueError("source frontmatter project_id does not match project")
        if frontmatter["source_id"] != change_set.source_id:
            raise ValueError("source frontmatter source_id does not match change set")
        if frontmatter["schema_version"] != change_set.schema_version:
            raise ValueError("source frontmatter schema_version does not match change set")
        if frontmatter["generation_mode"] != change_set.generation_mode.value:
            raise ValueError("source frontmatter generation_mode does not match change set")
        raw_path = frontmatter["raw_path"]
        if not raw_path.startswith("raw/") or not self._is_canonical_relative_path(raw_path):
            raise ValueError("source frontmatter raw_path is unsafe")
        if _SHA256_PATTERN.fullmatch(frontmatter["raw_sha256"]) is None:
            raise ValueError("source frontmatter raw_sha256 is invalid")

    @staticmethod
    def _parse_frontmatter(markdown: str) -> dict[str, Any]:
        if not markdown.startswith("---\n"):
            raise ValueError("source frontmatter is missing")
        closing_delimiter = markdown.find("\n---\n", len("---\n"))
        if closing_delimiter < 0:
            raise ValueError("source frontmatter is not closed")
        parsed = yaml.safe_load(markdown[len("---\n") : closing_delimiter])
        if not isinstance(parsed, dict):
            raise ValueError("source frontmatter must be a mapping")
        return parsed

    def _validate_links(self, markdown: str, changed_paths: set[str]) -> None:
        for match in _OBSIDIAN_LINK_PATTERN.finditer(markdown):
            target = match.group(1).split("|", maxsplit=1)[0].split("#", maxsplit=1)[0]
            if not target or not target.startswith("wiki/"):
                raise ValueError("Obsidian link target is not a governed Wiki path")
            candidates = {target}
            if not target.endswith(".md"):
                candidates.add(f"{target}.md")
            if not any(
                self._link_target_exists(candidate, changed_paths) for candidate in candidates
            ):
                raise ValueError(f"Obsidian link target does not exist: {target}")

    def _link_target_exists(self, candidate: str, changed_paths: set[str]) -> bool:
        if candidate in changed_paths:
            return True
        if not self._is_canonical_relative_path(candidate):
            return False
        target = (self.paths.project_root / candidate).resolve()
        return target.is_relative_to(self.paths.wiki_root) and target.is_file()

    @staticmethod
    def _is_canonical_relative_path(value: str) -> bool:
        path = Path(value)
        return not path.is_absolute() and "\\" not in value and all(
            part not in {"", ".", ".."} for part in path.parts
        )
