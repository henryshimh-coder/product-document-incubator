from __future__ import annotations

from src.application.dto.dashboard import (
    DashboardBaselineView,
    DashboardView,
    GetDashboardInput,
)
from src.application.ports.dashboard import ManifestIntegrity, ManifestReader
from src.application.ports.repositories import (
    ChangeRepository,
    EventRepository,
    IssueRepository,
    ProjectRepository,
    SourceRepository,
)


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
        manifest = self.manifest.read_and_validate()
        if manifest.project_id != command.project_id:
            raise ValueError("dashboard project does not match baseline manifest project")
        project = self.projects.get(command.project_id)
        baseline = DashboardBaselineView.from_manifest(manifest)
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
