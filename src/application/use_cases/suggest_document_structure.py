from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from src.application.dto.documents import SuggestStructureInput
from src.application.ports.repositories import ProjectRepository
from src.domain.enums import StructureSuggestionStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.incubator import StructureSuggestion
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.markdown_sections import extract_headings
from src.infrastructure.files.project_library import ProjectPaths


class StructureSuggestionRepository(Protocol):
    def add(self, suggestion: StructureSuggestion) -> None: ...

    def get(self, suggestion_id: str) -> StructureSuggestion: ...

    def list_for_project(self, project_id: str) -> list[StructureSuggestion]: ...

    def update(self, suggestion: StructureSuggestion) -> None: ...


class StructureSuggestionWorkflow(Protocol):
    def generate_suggestions(self, inputs: Mapping[str, Any]) -> dict[str, Any]: ...


class SuggestDocumentStructure:
    """Generate only outline-based suggestions from an explicit Owner selection."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        projects: ProjectRepository,
        suggestions: StructureSuggestionRepository,
        gateway: StructureSuggestionWorkflow,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.projects = projects
        self.suggestions = suggestions
        self.gateway = gateway
        self.now = now or (lambda: datetime.now(UTC))

    def execute(self, command: SuggestStructureInput) -> list[StructureSuggestion]:
        if command.project_id != self.paths.project_id:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH)
        self.projects.get(command.project_id)
        reference_ids = self._references(command.project_id, command.reference_project_ids)
        current_headings = self._headings_for(command.project_id)
        inputs = {
            "schema_version": "2.0",
            "task_type": "structure_suggestion",
            "project_id": command.project_id,
            "current_headings": current_headings,
            "reference_projects": [
                {"project_id": project_id, "headings": self._headings_for(project_id)}
                for project_id in reference_ids
            ],
        }
        response = self.gateway.generate_suggestions(inputs)
        now = self.now()
        results: list[StructureSuggestion] = []
        for item in response["result"]["suggestions"]:
            references = list(item["reference_project_ids"])
            if not set(references).issubset(reference_ids):
                raise DomainError(
                    ErrorCode.MODEL_OUTPUT_INVALID,
                    "STRUCTURE_REFERENCE_UNAUTHORIZED",
                )
            suggestion = StructureSuggestion(
                id=f"SUG-{uuid4().hex.upper()}",
                project_id=command.project_id,
                title=item["title"],
                reason=item["reason"],
                reference_project_ids=references,
                confidence=item["confidence"],
                status=StructureSuggestionStatus.OPEN,
                created_at=now,
                updated_at=now,
            )
            self.suggestions.add(suggestion)
            results.append(suggestion)
        return results

    def list(self, project_id: str) -> list[StructureSuggestion]:
        if project_id != self.paths.project_id:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH)
        return self.suggestions.list_for_project(project_id)

    def accept(self, *, project_id: str, suggestion_id: str) -> StructureSuggestion:
        return self._set_status(project_id, suggestion_id, StructureSuggestionStatus.ACCEPTED)

    def ignore(self, *, project_id: str, suggestion_id: str) -> StructureSuggestion:
        return self._set_status(project_id, suggestion_id, StructureSuggestionStatus.IGNORED)

    def _set_status(
        self,
        project_id: str,
        suggestion_id: str,
        status: StructureSuggestionStatus,
    ) -> StructureSuggestion:
        if project_id != self.paths.project_id:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH)
        suggestion = self.suggestions.get(suggestion_id)
        if suggestion.project_id != project_id:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH)
        updated = suggestion.model_copy(update={"status": status, "updated_at": self.now()})
        self.suggestions.update(updated)
        return updated

    def _references(self, project_id: str, requested: list[str]) -> list[str]:
        references: list[str] = []
        for reference_id in requested:
            if reference_id == project_id:
                raise ValueError("REFERENCE_PROJECT_SELF")
            if reference_id not in references:
                self.projects.get(reference_id)
                references.append(reference_id)
        return references

    def _headings_for(self, project_id: str) -> list[str]:
        paths = ProjectPaths.for_project(self.paths.library_root, project_id)
        if not paths.manifest_path.is_file():
            raise DomainError(ErrorCode.BASELINE_NOT_FOUND, f"PROJECT={project_id}")
        manifest = ManifestStore(
            paths.manifest_path, project_root=paths.project_root
        ).read_and_validate()
        if manifest.project_id != project_id:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "MANIFEST_PROJECT_MISMATCH")
        document = (paths.project_root / manifest.full_document_path).resolve()
        if (
            not document.is_relative_to((paths.wiki_root / "versions").resolve())
            or not document.is_file()
        ):
            raise DomainError(ErrorCode.BASELINE_NOT_FOUND, f"PROJECT={project_id}")
        return extract_headings(document.read_text(encoding="utf-8"))
