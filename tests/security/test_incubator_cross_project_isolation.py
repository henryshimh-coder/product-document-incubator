from __future__ import annotations

import pytest

from src.application.dto.documents import ExportCurrentDocumentInput
from src.application.use_cases.export_current_document import ExportCurrentDocument
from src.domain.errors import DomainError
from src.infrastructure.files.manifest_store import ManifestStore
from tests.e2e.test_incubator_full_success import IncubatorHarness


def test_project_a_export_service_rejects_project_b_command(tmp_path) -> None:
    harness = IncubatorHarness(tmp_path)
    project_a = harness.create_project("PROJECT_A", "产品 A")
    project_b = harness.create_project("PROJECT_B", "产品 B")
    exporter = ExportCurrentDocument(
        paths=project_a,
        projects=harness.projects,
        manifest=ManifestStore(project_a.manifest_path, project_root=project_a.project_root),
    )

    with pytest.raises(DomainError, match="RELEASE_PROJECT_MISMATCH"):
        exporter.execute(ExportCurrentDocumentInput(project_id=project_b.project_id))
