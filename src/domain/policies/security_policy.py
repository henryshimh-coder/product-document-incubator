from __future__ import annotations

from src.domain.enums import SecurityLevel
from src.domain.models import Project, SourceRecord


def can_call_external_model(project: Project, source: SourceRecord) -> bool:
    return all(
        (
            project.allow_external_model,
            source.allow_external_model,
            source.is_redacted,
            source.security_level
            in {
                SecurityLevel.L1_PUBLIC_SIMULATED,
                SecurityLevel.L2_INTERNAL,
            },
            not source.is_sandbox or source.security_level == SecurityLevel.L1_PUBLIC_SIMULATED,
        )
    )
