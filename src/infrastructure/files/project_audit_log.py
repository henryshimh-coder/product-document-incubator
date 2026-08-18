from __future__ import annotations

from datetime import datetime

from src.infrastructure.files.project_library import ProjectPaths


class ProjectAuditLog:
    def __init__(self, paths: ProjectPaths) -> None:
        self.path = paths.wiki_root / "log.md"

    def append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    @staticmethod
    def render_ingest(
        existing: str,
        *,
        transaction_id: str,
        idempotency_key: str,
        source_id: str,
        committed_at: datetime,
    ) -> str:
        if len(idempotency_key) != 64 or any(
            character not in "0123456789abcdef" for character in idempotency_key
        ):
            raise ValueError("WIKI_IDEMPOTENCY_KEY_INVALID")
        for value in (transaction_id, source_id):
            if not value or any(character in value for character in "\r\n|"):
                raise ValueError("WIKI_AUDIT_FIELD_INVALID")
        if idempotency_key in existing:
            return existing
        line = (
            f"- {committed_at.isoformat()} | Wiki Ingest | {source_id} | "
            f"{transaction_id} | {idempotency_key}"
        )
        return f"{existing.rstrip()}\n\n{line}\n"
