from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import SecurityLevel
from src.domain.models import SourceRecord
from src.infrastructure.files.project_library import ProjectPaths

_CITATION = re.compile(r"【(?P<source_id>[A-Za-z0-9][A-Za-z0-9_-]{0,127})[：:][^】]+】")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")


class _SourceReading(Protocol):
    def get(self, source_id: str) -> SourceRecord: ...


class SafeWikiTopicInput(BaseModel):
    """The only topic representation permitted to cross the model boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=256)
    markdown: str = Field(min_length=1, max_length=10_000)
    source_ids: list[str] = Field(min_length=1, max_length=50)


class WikiOutboundProjection(BaseModel):
    """Safe context plus local-only signals; excluded page metadata is never retained."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    safe_index_projection: str = Field(max_length=5_000)
    safe_related_topics: list[SafeWikiTopicInput] = Field(max_length=20)
    local_sensitive_comparison_required: bool
    excluded_topic_count: int = Field(ge=0)


class WikiOutboundContextBuilder:
    """Build a fail-closed, source-authorized projection of selected Wiki topics."""

    def __init__(self, paths: ProjectPaths, sources: _SourceReading) -> None:
        self.paths = paths
        self.sources = sources
        self.project_root = paths.project_root.resolve()
        self.topics_root = (paths.wiki_root / "topics").resolve()

    def build(
        self,
        project_id: str,
        related_topic_paths: Sequence[str],
    ) -> WikiOutboundProjection:
        if project_id != self.paths.project_id:
            raise ValueError("WIKI_OUTBOUND_PROJECT_MISMATCH")
        if isinstance(related_topic_paths, (str, bytes)):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        paths = tuple(related_topic_paths)
        if len(paths) != len(set(paths)) or len(paths) > 20:
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")

        project_allowed = self._project_allows_external_model(project_id)
        safe_topics: list[SafeWikiTopicInput] = []
        excluded_count = 0
        for relative_path in paths:
            topic_path = self._topic_path(relative_path)
            topic = self._safe_topic(topic_path, project_id, project_allowed)
            if topic is None:
                excluded_count += 1
            else:
                safe_topics.append(topic)

        index_projection = "\n".join(
            f"- {topic.title} [{', '.join(topic.source_ids)}]" for topic in safe_topics
        )
        return WikiOutboundProjection(
            safe_index_projection=index_projection,
            safe_related_topics=safe_topics,
            local_sensitive_comparison_required=excluded_count > 0,
            excluded_topic_count=excluded_count,
        )

    def _project_allows_external_model(self, project_id: str) -> bool:
        project_path = self.paths.system_root / "project.json"
        try:
            payload = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return bool(
            isinstance(payload, dict)
            and payload.get("project_id") == project_id
            and payload.get("allow_external_model") is True
        )

    def _topic_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        path = Path(relative_path)
        if (
            path.is_absolute()
            or "\\" in relative_path
            or path.as_posix() != relative_path
            or not relative_path.startswith("wiki/topics/")
            or not relative_path.endswith(".md")
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        lexical_target = self.paths.project_root / relative_path
        target = lexical_target.resolve()
        if (
            lexical_target.is_symlink()
            or not target.is_relative_to(self.project_root)
            or not target.is_relative_to(self.topics_root)
            or not target.is_file()
        ):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        return target

    def _safe_topic(
        self,
        path: Path,
        project_id: str,
        project_allowed: bool,
    ) -> SafeWikiTopicInput | None:
        if not project_allowed:
            return None
        try:
            text = path.read_text(encoding="utf-8")
            frontmatter, body = self._parse_topic(text)
        except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError):
            return None
        if frontmatter.get("project_id") != project_id:
            return None
        title = frontmatter.get("topic_id")
        if not isinstance(title, str) or not title.strip():
            return None

        statements: list[str] = []
        source_ids: list[str] = []
        for line in body.splitlines():
            matches = list(_CITATION.finditer(line))
            if not matches:
                continue
            statement = _BULLET.sub("", _CITATION.sub("", line)).strip()
            if not statement or statement.startswith("#"):
                return None
            statements.append(statement)
            for match in matches:
                source_id = match.group("source_id")
                if source_id not in source_ids:
                    source_ids.append(source_id)
        if not statements or not source_ids:
            return None
        if any(not self._source_is_exportable(source_id, project_id) for source_id in source_ids):
            return None
        return SafeWikiTopicInput(
            title=title,
            markdown="\n".join(statements),
            source_ids=source_ids,
        )

    def _source_is_exportable(self, source_id: str, project_id: str) -> bool:
        try:
            source = self.sources.get(source_id)
        except (KeyError, TypeError, ValueError):
            return False
        return all(
            (
                source.project_id == project_id,
                source.security_level
                in {SecurityLevel.L1_PUBLIC_SIMULATED, SecurityLevel.L2_INTERNAL},
                source.is_redacted,
                source.allow_external_model,
                not source.is_sandbox
                or source.security_level == SecurityLevel.L1_PUBLIC_SIMULATED,
            )
        )

    @staticmethod
    def _parse_topic(markdown: str) -> tuple[dict[str, object], str]:
        if not markdown.startswith("---\n"):
            raise ValueError("WIKI_OUTBOUND_TOPIC_FRONTMATTER_INVALID")
        closing = markdown.find("\n---\n", len("---\n"))
        if closing < 0:
            raise ValueError("WIKI_OUTBOUND_TOPIC_FRONTMATTER_INVALID")
        frontmatter = yaml.safe_load(markdown[len("---\n") : closing])
        if not isinstance(frontmatter, dict):
            raise ValueError("WIKI_OUTBOUND_TOPIC_FRONTMATTER_INVALID")
        return frontmatter, markdown[closing + len("\n---\n") :]
