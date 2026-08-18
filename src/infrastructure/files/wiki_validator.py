from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from src.domain.errors import DomainError, ErrorCode
from src.domain.models import SourceRecord
from src.domain.wiki import WikiChangeSet, WikiTargetPlan
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
_SAFE_PATH_SEGMENT = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff_-]+")
_TARGET_PLAN_CAPABILITY = object()


class WikiTargetPlanner:
    """Derive authorized Wiki targets from trusted local source and topic inputs."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths

    def build(
        self,
        source: SourceRecord,
        *,
        existing_topic_paths: Sequence[str],
        new_topic_titles: Sequence[str],
    ) -> WikiTargetPlan:
        if source.project_id != self.paths.project_id:
            raise ValueError("WIKI_TARGET_PLAN_SOURCE_PROJECT_MISMATCH")
        source_name = source.material_name or Path(source.original_filename).stem
        source_page_path = (
            f"wiki/sources/{self._safe_segment(source.id)}-"
            f"{self._safe_segment(source_name)}.md"
        )
        topic_paths = [self._existing_topic_path(path) for path in existing_topic_paths]
        topic_paths.extend(self._new_topic_path(title) for title in new_topic_titles)
        if len(topic_paths) != len(set(topic_paths)):
            raise ValueError("WIKI_TARGET_PLAN_TOPIC_DUPLICATE")
        return WikiTargetPlan._from_trusted(
            capability=_TARGET_PLAN_CAPABILITY,
            project_id=source.project_id,
            source_id=source.id,
            source_page_path=source_page_path,
            topic_page_paths=tuple(topic_paths),
        )

    def _existing_topic_path(self, relative_path: str) -> str:
        if not self._is_topic_path(relative_path):
            raise ValueError("WIKI_TARGET_PLAN_TOPIC_UNAUTHORIZED")
        target = (self.paths.project_root / relative_path).resolve()
        if not target.is_relative_to(self.paths.wiki_root) or not target.is_file():
            raise ValueError("WIKI_TARGET_PLAN_TOPIC_UNAUTHORIZED")
        return relative_path

    def _new_topic_path(self, title: str) -> str:
        return f"wiki/topics/{self._safe_segment(title)}.md"

    @staticmethod
    def _safe_segment(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("WIKI_TARGET_PLAN_SEGMENT_INVALID")
        normalized = _SAFE_PATH_SEGMENT.sub("-", value.strip()).strip("-_")
        if not normalized:
            raise ValueError("WIKI_TARGET_PLAN_SEGMENT_INVALID")
        return normalized

    @staticmethod
    def _is_topic_path(relative_path: str) -> bool:
        return (
            isinstance(relative_path, str)
            and relative_path.startswith("wiki/topics/")
            and relative_path.endswith(".md")
            and Path(relative_path).as_posix() == relative_path
            and all(part not in {"", ".", ".."} for part in Path(relative_path).parts)
        )


class WikiValidator:
    """Reject unsafe or internally inconsistent Wiki change sets before commit."""

    def __init__(self, paths: ProjectPaths, target_plan: WikiTargetPlan) -> None:
        self.paths = paths
        self.target_plan = target_plan

    def validate_change_set(self, change_set: WikiChangeSet) -> None:
        try:
            change_set.validate_contract()
            self._validate_project(change_set)
            changed_paths = {change.relative_path for change in change_set.page_changes}
            self._validate_authorized_targets(change_set, changed_paths)
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

    def _validate_authorized_targets(
        self, change_set: WikiChangeSet, changed_paths: set[str]
    ) -> None:
        is_trusted_plan = isinstance(self.target_plan, WikiTargetPlan) and (
            self.target_plan._is_authorized_by(_TARGET_PLAN_CAPABILITY)
        )
        if not is_trusted_plan:
            raise ValueError("WIKI_CHANGESET_TARGET_PLAN_UNTRUSTED")
        if self.target_plan.project_id != self.paths.project_id:
            raise ValueError("target plan project_id does not match resolved project root")
        if self.target_plan.source_id != change_set.source_id:
            raise ValueError("target plan source_id does not match change set")
        expected_paths = {
            "wiki/index.md",
            "wiki/log.md",
            ".incubator/source-index.json",
            self.target_plan.source_page_path,
            *self.target_plan.topic_page_paths,
        }
        if changed_paths != expected_paths:
            raise ValueError("WIKI_CHANGESET_TARGET_UNAUTHORIZED")
        if change_set.source_page_path != self.target_plan.source_page_path:
            raise ValueError("WIKI_CHANGESET_TARGET_UNAUTHORIZED")
        if set(change_set.topic_page_paths) != set(self.target_plan.topic_page_paths):
            raise ValueError("WIKI_CHANGESET_TARGET_UNAUTHORIZED")

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
