from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from src.domain.models import BaselineManifest, Project
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteProjectRepository
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.project_library import ProjectPaths

NOW = datetime(2026, 8, 12, tzinfo=UTC)


class SuggestionEnvironment:
    def __init__(self, tmp_path: Path) -> None:
        self.library_root = tmp_path / "library"
        self.db_path = self.library_root / ".incubator/product_incubator.db"
        migrate(self.db_path)
        self.projects = SqliteProjectRepository(self.db_path)
        self.gateway = _Gateway()
        for project_id, name, markdown in (
            ("A", "项目 A", "# A\n\n## 产品概述\n\nA 的正文不得外发。"),
            (
                "B",
                "项目 B",
                "# B\n\n## 产品概述\n\n## 业务流程\n\n### 风险边界\n\nB 的正文不得外发。",
            ),
            ("C", "项目 C", "# C\n\n## 未授权内容\n\nC 的正文不得外发。"),
        ):
            self.projects.add(
                Project(
                    id=project_id,
                    name=name,
                    product_line="测试",
                    stage="进行中",
                    current_baseline_id=None,
                    allow_external_model=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            self._write_current(project_id, markdown)
        from src.application.use_cases.suggest_document_structure import SuggestDocumentStructure
        from src.infrastructure.db.repositories import SqliteStructureSuggestionRepository

        self.paths = ProjectPaths.for_project(self.library_root, "A")
        self.current_a = self.paths.wiki_root / "current" / "当前产品方案.md"
        self.current_a_before = self.current_a.read_bytes()
        self.service = SuggestDocumentStructure(
            paths=self.paths,
            projects=self.projects,
            suggestions=SqliteStructureSuggestionRepository(self.db_path),
            gateway=self.gateway,
            now=lambda: NOW,
        )

    def _write_current(self, project_id: str, markdown: str) -> None:
        paths = ProjectPaths.for_project(self.library_root, project_id)
        current = paths.wiki_root / "current" / "当前产品方案.md"
        version = paths.wiki_root / "versions" / f"{project_id}-01" / "产品方案.md"
        current.parent.mkdir(parents=True, exist_ok=True)
        version.parent.mkdir(parents=True, exist_ok=True)
        payload = markdown.encode("utf-8")
        current.write_bytes(payload)
        version.write_bytes(payload)
        paths.system_root.mkdir(parents=True, exist_ok=True)
        ManifestStore(paths.manifest_path, project_root=paths.project_root).atomic_replace(
            BaselineManifest(
                schema_version="2.0",
                project_id=project_id,
                current_baseline_id=f"BASE-{project_id}-01",
                current_version=f"{project_id}-01",
                parent_baseline_id=None,
                full_document_path=f"wiki/versions/{project_id}-01/产品方案.md",
                card_snapshot_path=f"wiki/versions/{project_id}-01/cards.json",
                full_document_sha256=hashlib.sha256(payload).hexdigest(),
                card_snapshot_sha256="a" * 64,
                change_request_id=None,
                approved_by="Owner",
                published_at=NOW,
                display_version="1.0",
            )
        )


class _Gateway:
    def __init__(self) -> None:
        self.last_input: dict = {}

    def generate_suggestions(self, inputs: dict) -> dict:
        self.last_input = inputs
        return {
            "workflow_run_id": "WF-STRUCTURE",
            "result": {
                "suggestions": [
                    {
                        "title": "风险边界",
                        "reason": "参考项目均包含风险边界章节。",
                        "reference_project_ids": ["B"],
                        "confidence": 0.9,
                    }
                ]
            },
        }


def test_suggestion_sends_only_explicitly_authorized_project_outlines(tmp_path: Path) -> None:
    from src.application.dto.documents import SuggestStructureInput

    env = SuggestionEnvironment(tmp_path)
    suggestions = env.service.execute(
        SuggestStructureInput(project_id="A", reference_project_ids=["B"], requested_by="Owner")
    )

    assert env.gateway.last_input["reference_projects"] == [
        {"project_id": "B", "headings": ["B", "产品概述", "业务流程", "风险边界"]}
    ]
    assert "C" not in str(env.gateway.last_input)
    assert "document_markdown" not in env.gateway.last_input
    assert env.current_a.read_bytes() == env.current_a_before
    assert suggestions[0].reference_project_ids == ["B"]


def test_accepting_suggestion_only_updates_its_status(tmp_path: Path) -> None:
    from src.application.dto.documents import SuggestStructureInput

    env = SuggestionEnvironment(tmp_path)
    suggestion = env.service.execute(
        SuggestStructureInput(project_id="A", reference_project_ids=["B"], requested_by="Owner")
    )[0]

    accepted = env.service.accept(project_id="A", suggestion_id=suggestion.id)

    assert accepted.status.value == "accepted"
    assert env.current_a.read_bytes() == env.current_a_before
