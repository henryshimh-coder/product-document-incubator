from __future__ import annotations


class ReleaseGuard:
    """Process-local publish kill switch backed by startup reconciliation.

    The guard is re-evaluated from the authoritative manifest on every
    container build, so an in-memory flag is sufficient: a blocked state that
    persists on disk is always rediscovered by ``validate_manifest_mirror``.
    """

    def __init__(self) -> None:
        self._reason: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self._reason is not None

    @property
    def reason(self) -> str | None:
        return self._reason

    def block(self, reason: str) -> None:
        self._reason = reason.strip() or "manifest_sqlite_mismatch"

    def clear(self) -> None:
        self._reason = None
