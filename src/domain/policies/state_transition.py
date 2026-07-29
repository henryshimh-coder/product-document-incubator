from __future__ import annotations

from src.domain.enums import ChangeStatus
from src.domain.errors import DomainError

CHANGE_TRANSITIONS: dict[ChangeStatus, frozenset[ChangeStatus]] = {
    ChangeStatus.DRAFT: frozenset(
        {
            ChangeStatus.PENDING_APPROVAL,
            ChangeStatus.DEFERRED,
            ChangeStatus.NEEDS_INFO,
        }
    ),
    ChangeStatus.PENDING_APPROVAL: frozenset(
        {
            ChangeStatus.APPROVED,
            ChangeStatus.REJECTED,
            ChangeStatus.DEFERRED,
            ChangeStatus.NEEDS_INFO,
        }
    ),
    ChangeStatus.APPROVED: frozenset({ChangeStatus.PUBLISHED}),
    ChangeStatus.NEEDS_INFO: frozenset({ChangeStatus.DRAFT}),
    ChangeStatus.DEFERRED: frozenset({ChangeStatus.DRAFT}),
}


def ensure_change_transition(current: ChangeStatus, target: ChangeStatus) -> None:
    if target not in CHANGE_TRANSITIONS.get(current, frozenset()):
        raise DomainError(
            "INVALID_CHANGE_TRANSITION",
            f"{current.value} -> {target.value}",
        )
