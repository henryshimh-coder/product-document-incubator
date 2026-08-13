from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.use_cases.test_suggest_document_structure import SuggestionEnvironment


def test_structure_suggestion_rejects_current_project_as_reference(tmp_path: Path) -> None:
    from src.application.dto.documents import SuggestStructureInput

    env = SuggestionEnvironment(tmp_path)

    with pytest.raises(ValueError, match="REFERENCE_PROJECT_SELF"):
        env.service.execute(
            SuggestStructureInput(project_id="A", reference_project_ids=["A"], requested_by="Owner")
        )

    assert env.gateway.last_input == {}
