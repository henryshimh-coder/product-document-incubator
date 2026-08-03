from __future__ import annotations

from src.application.dto.dashboard import DashboardView, GetDashboardInput
from src.application.ports.dashboard import ManifestIntegrity, ManifestReader
from src.application.ports.repositories import (
    ChangeRepository,
    EventRepository,
    IssueRepository,
    ProjectRepository,
    SourceRepository,
)
from src.domain.enums import BaselineStatus
from src.domain.models import Baseline


class GetDashboard:
    def __init__(
        self,
        *,
        manifest: ManifestReader,
        integrity: ManifestIntegrity,
        projects: ProjectRepository,
        issues: IssueRepository,
        changes: ChangeRepository,
        sources: SourceRepository,
        events: EventRepository,
    ) -> None:
        self.manifest = manifest
        self.integrity = integrity
        self.projects = projects
        self.issues = issues
        self.changes = changes
        self.sources = sources
        self.events = events

    def execute(self, command: GetDashboardInput) -> DashboardView:
        snapshot = self.manifest.read_snapshot()
        manifest = snapshot.manifest
        if manifest.project_id != command.project_id:
            raise ValueError("dashboard project does not match baseline manifest project")
        project = self.projects.get(command.project_id)
        baseline = Baseline(
            id=manifest.current_baseline_id,
            project_id=manifest.project_id,
            version=manifest.current_version,
            parent_baseline_id=manifest.parent_baseline_id,
            status=BaselineStatus.EFFECTIVE,
            full_document_path=manifest.full_document_path,
            card_snapshot_path=manifest.card_snapshot_path,
            manifest_sha256=snapshot.sha256,
            change_request_id=manifest.change_request_id,
            approved_by=manifest.approved_by,
            effective_at=manifest.published_at,
            created_at=manifest.published_at,
        )
        integrity_ok = self.integrity.validate(manifest)
        return DashboardView(
            project=project,
            current_baseline=baseline,
            open_issue_count=len(self.issues.list_open(command.project_id)),
            candidate_change_count=len(self.changes.list_pending(command.project_id)),
            source_count=len(self.sources.list_for_project(command.project_id)),
            recent_events=[
                event.model_dump(mode="python")
                for event in self.events.latest(command.project_id, limit=5)
            ],
            integrity_ok=integrity_ok,
        )
