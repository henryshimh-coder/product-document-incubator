from __future__ import annotations

from src.application.dto.materials import (
    DeleteArchivedSourceInput,
    DeletedArchivedSourceView,
)
from src.application.ports.repositories import SourceRepository
from src.domain.errors import DomainError, ErrorCode
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.source_index_store import SourceIndexStore
from src.infrastructure.files.source_trash import SourceTrash, SourceTrashTransaction


class DeleteArchivedSource:
    ALLOWED_STATUSES = frozenset({"pending_ingest", "ingest_failed"})

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        sources: SourceRepository,
        index: SourceIndexStore,
        trash: SourceTrash,
    ) -> None:
        self.paths = paths
        self.sources = sources
        self.index = index
        self.trash = trash

    def execute(self, command: DeleteArchivedSourceInput) -> DeletedArchivedSourceView:
        if command.project_id != self.paths.project_id:
            raise ValueError("MATERIAL_PROJECT_MISMATCH")
        source = self.sources.get(command.source_id)
        if source.project_id != command.project_id:
            raise ValueError("MATERIAL_PROJECT_MISMATCH")
        if source.ingest_status not in self.ALLOWED_STATUSES:
            raise DomainError(
                ErrorCode.MATERIAL_DELETE_NOT_ALLOWED,
                f"status={source.ingest_status}",
            )
        transaction: SourceTrashTransaction | None = None
        try:
            transaction = self.trash.move(source)
            self.index.remove(source.id)
            self.sources.delete(source.id, source.project_id)
        except BaseException as error:
            if transaction is None and isinstance(error, ValueError):
                raise
            rollback_errors: list[BaseException] = []
            if transaction is not None:
                try:
                    self.trash.restore(transaction)
                except BaseException as rollback_error:
                    rollback_errors.append(rollback_error)
            try:
                self.index.upsert(source)
            except BaseException as rollback_error:
                rollback_errors.append(rollback_error)
            code = (
                "MATERIAL_DELETE_ROLLBACK_FAILED" if rollback_errors else "MATERIAL_DELETE_FAILED"
            )
            raise RuntimeError(code) from error
        return DeletedArchivedSourceView(
            source_id=source.id,
            trash_path=transaction.trash_directory,
            sha256=transaction.sha256,
        )
