from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.domain.services.citation_validator import CitationValidator
from src.infrastructure.gateways._common import invoke, validate_input
from src.infrastructure.gateways.schemas import QueryWorkflowInput, QueryWorkflowOutput

INSUFFICIENT_EVIDENCE_ANSWER = "现有证据不足，无法给出确定性结论。"


class QueryGateway:
    def __init__(self, client: WorkflowGateway) -> None:
        self.client = client

    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        user: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        validated_inputs = validate_input(
            QueryWorkflowInput,
            inputs,
            invalid_detail="QUERY_INPUT_INVALID",
        )
        workflow_run_id, raw_output = invoke(self.client, validated_inputs, user, timeout_seconds)
        try:
            output = QueryWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("QUERY_OUTPUT_INVALID") from error
        if output.baseline_version != validated_inputs["baseline_version"]:
            raise OutputValidationError("BASELINE_VERSION_MISMATCH")
        trusted_citations = validated_inputs["citations"]
        if not isinstance(trusted_citations, list):
            raise OutputValidationError("QUERY_INPUT_INVALID")
        validator = CitationValidator(trusted_citations)
        for citation in output.citations:
            validator.validate(citation.model_dump(mode="json"))
        trusted_rule_ids = {card["id"] for card in validated_inputs["effective_cards"]}
        if not set(output.effective_rules) <= trusted_rule_ids:
            raise OutputValidationError("UNKNOWN_EFFECTIVE_RULE")
        trusted_notices = {
            notice_type: {
                notice["summary"]
                for notice in validated_inputs["notices"]
                if notice["type"] == notice_type
            }
            for notice_type in ("candidate", "conflict")
        }
        if (
            output.candidate_notice is not None
            and output.candidate_notice not in trusted_notices["candidate"]
        ):
            raise OutputValidationError("UNKNOWN_CANDIDATE_NOTICE")
        if (
            output.conflict_notice is not None
            and output.conflict_notice not in trusted_notices["conflict"]
        ):
            raise OutputValidationError("UNKNOWN_CONFLICT_NOTICE")
        if not any(
            validator.has_direct_support(output.answer, citation.model_dump(mode="json"))
            for citation in output.citations
        ):
            output = output.model_copy(
                update={
                    "answer": INSUFFICIENT_EVIDENCE_ANSWER,
                    "evidence_sufficiency": "insufficient",
                }
            )
        return {
            "workflow_run_id": workflow_run_id,
            "result": output.model_dump(mode="json"),
        }
