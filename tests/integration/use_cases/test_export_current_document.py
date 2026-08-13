from __future__ import annotations

from pathlib import Path

import pytest

from src.domain.errors import DomainError
from tests.integration.use_cases.test_publish_document_draft import (
    PublishEnvironment,
    _command,
)


def test_export_download_bytes_equal_current_markdown(tmp_path: Path) -> None:
    from src.application.dto.documents import ExportCurrentDocumentInput
    from src.application.use_cases.export_current_document import ExportCurrentDocument

    env = PublishEnvironment(tmp_path)
    env.publish.execute(_command())
    export = ExportCurrentDocument(paths=env.paths, projects=env.projects, manifest=env.manifest)

    exported = export.execute(ExportCurrentDocumentInput(project_id="NEW"))

    assert exported.filename == "新产品_产品方案_1.0.md"
    assert exported.content == env.current_path.read_bytes()
    assert exported.export_path.read_bytes() == exported.content


def test_export_is_unavailable_without_current_baseline(tmp_path: Path) -> None:
    from src.application.dto.documents import ExportCurrentDocumentInput
    from src.application.use_cases.export_current_document import ExportCurrentDocument

    env = PublishEnvironment(tmp_path)
    export = ExportCurrentDocument(paths=env.paths, projects=env.projects, manifest=env.manifest)

    with pytest.raises(DomainError, match="BASELINE_NOT_FOUND"):
        export.execute(ExportCurrentDocumentInput(project_id="NEW"))
