from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from src.application.dto.materials import ReclassifySourceInput
from src.application.ports.repositories import SourceRepository
from src.infrastructure.files.project_audit_log import ProjectAuditLog
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.source_index_store import SourceIndexStore


class ReclassifySource:
    def __init__(
        self,
        *,
        paths: ProjectPaths,
        sources: SourceRepository,
        index: SourceIndexStore,
        audit: ProjectAuditLog | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths, self.sources, self.index = paths, sources, index
        self.audit = audit or ProjectAuditLog(paths)
        self.now = now or (lambda: datetime.now(UTC))

    def execute(self, command: ReclassifySourceInput):
        if command.project_id != self.paths.project_id:
            raise ValueError("MATERIAL_PROJECT_MISMATCH")
        before = self.sources.get(command.source_id)
        if before.project_id != command.project_id:
            raise ValueError("MATERIAL_PROJECT_MISMATCH")
        if before.source_type in {
            "product_requirement",
            "business_rule",
            "customer_market_material",
            "meeting_minutes",
            "risk_compliance",
            "technical_specification",
            "operation_feedback",
            "other",
        }:
            raise ValueError("MATERIAL_TYPE_ALREADY_STANDARD")
        after = before.model_copy(update={"source_type": command.new_source_type})
        self.sources.update(after)
        try:
            self.index.upsert(after)
            self.audit.append(
                f"{self.now().isoformat()} | 分类调整 | {before.id} | "
                f"{before.source_type} -> {after.source_type} | {command.owner_name}"
            )
        except (OSError, ValueError):
            self.sources.update(before)
            try:
                self.index.upsert(before)
            except (OSError, ValueError):
                pass
            raise RuntimeError("SOURCE_RECLASSIFY_FAILED") from None
        return after
