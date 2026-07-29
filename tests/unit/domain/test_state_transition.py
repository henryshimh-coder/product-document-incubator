from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("current_value", "target_value"),
    [
        ("draft", "published"),
        ("rejected", "approved"),
        ("published", "draft"),
    ],
)
def test_invalid_change_transition_is_rejected(current_value, target_value):
    """Catches bypassing review or reopening a terminal change state."""
    enums = importlib.import_module("src.domain.enums")
    errors = importlib.import_module("src.domain.errors")
    transition = importlib.import_module("src.domain.policies.state_transition")

    with pytest.raises(errors.DomainError, match="INVALID_CHANGE_TRANSITION"):
        transition.ensure_change_transition(
            enums.ChangeStatus(current_value),
            enums.ChangeStatus(target_value),
        )


@pytest.mark.parametrize(
    ("current_value", "target_value"),
    [
        ("draft", "pending_approval"),
        ("draft", "deferred"),
        ("draft", "needs_info"),
        ("pending_approval", "approved"),
        ("pending_approval", "rejected"),
        ("pending_approval", "deferred"),
        ("pending_approval", "needs_info"),
        ("approved", "published"),
        ("needs_info", "draft"),
        ("deferred", "draft"),
    ],
)
def test_governed_change_transition_is_allowed(current_value, target_value):
    """Catches removing a valid step from the review and release workflow."""
    enums = importlib.import_module("src.domain.enums")
    transition = importlib.import_module("src.domain.policies.state_transition")

    transition.ensure_change_transition(
        enums.ChangeStatus(current_value),
        enums.ChangeStatus(target_value),
    )
