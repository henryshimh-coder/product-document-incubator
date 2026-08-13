from __future__ import annotations

from pathlib import Path


def test_reconciliation_rebuilds_document_current_mirror_from_manifest(tmp_path: Path) -> None:
    from src.infrastructure.recovery.reconciliation_service import ReconciliationService
    from tests.integration.use_cases.test_publish_document_draft import (
        PublishEnvironment,
        _command,
    )

    env = PublishEnvironment(tmp_path)
    baseline = env.publish.execute(_command())
    version_path = env.paths.project_root / baseline.full_document_path
    env.current_path.unlink()
    service = ReconciliationService(
        manifest_store=env.manifest,
        db_path=env.paths.library_root / ".incubator/product_incubator.db",
        project_root=env.paths.project_root,
    )

    result = service.rebuild_current_from_manifest()

    assert result.success is True
    assert env.current_path.read_bytes() == version_path.read_bytes()
