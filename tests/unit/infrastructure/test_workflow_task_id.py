"""Regression tests for the workflow task ID factory.

Random bare-hex IDs used to hit REDACTION_PATTERNS (phone/id_card/bank_card)
with ~0.6% probability each, which made the outbound safety proof fail closed
at random. The 4-char grouped format bounds digit runs to 4.
"""

from __future__ import annotations

import re

from src.infrastructure.files.redactor import REDACTION_PATTERNS
from src.infrastructure.gateways._common import new_workflow_task_id

_SHAPE = re.compile(r"^TASK-[A-Z]+-(?:[0-9A-F]{4}-){7}[0-9A-F]{4}$")
_DIGIT_RUN = re.compile(r"\d+")


def test_new_workflow_task_id_shape_and_digit_run_bound() -> None:
    for _ in range(500):
        task_id = new_workflow_task_id("TASK-QUERY")
        assert _SHAPE.match(task_id), task_id
        assert max(len(run.group(0)) for run in _DIGIT_RUN.finditer(task_id)) <= 4


def test_new_workflow_task_id_never_matches_sensitive_patterns() -> None:
    for _ in range(500):
        task_id = new_workflow_task_id("TASK-LINT")
        assert not any(pattern.search(task_id) for pattern in REDACTION_PATTERNS.values()), task_id
