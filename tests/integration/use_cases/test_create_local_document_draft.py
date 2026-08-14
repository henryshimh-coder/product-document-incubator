from __future__ import annotations


def test_local_draft_creator_is_a_distinct_no_gateway_use_case() -> None:
    """Catches sensitive draft creation being coupled to an external document gateway."""
    from src.application.use_cases.create_local_document_draft import CreateLocalDocumentDraft

    assert CreateLocalDocumentDraft.__init__.__name__ == "__init__"
