from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.domain.services.citation_validator import (
    CitationValidator,
    all_claims_have_direct_support,
    contains_normalized_statement,
)
from src.infrastructure.gateways._common import OutboundSafetyProof, invoke, validate_input
from src.infrastructure.gateways.schemas import QueryWorkflowInput, QueryWorkflowOutput

INSUFFICIENT_EVIDENCE_ANSWER = "现有证据不足，无法给出确定性结论。"


class QueryGateway:
    def __init__(self, client: WorkflowGateway, *, timeout_seconds: int) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        safety_proof: OutboundSafetyProof,
        user: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        validated_inputs = validate_input(
            QueryWorkflowInput,
            inputs,
            invalid_detail="QUERY_INPUT_INVALID",
            safety_proof=safety_proof,
        )
        # 超时一律来自组合根注入的配置（T15-R01）；调用方仅可在测试等
        # 受控场景显式覆盖。
        effective_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        workflow_run_id, raw_output = invoke(self.client, validated_inputs, user, effective_timeout)
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
        returned_citation_ids = {citation.id for citation in output.citations}
        citation_ids_by_rule = {
            card["id"]: set(card["source_citations"])
            for card in validated_inputs["effective_cards"]
        }
        for rule_id in output.effective_rules:
            if not returned_citation_ids & citation_ids_by_rule[rule_id]:
                raise OutputValidationError("EFFECTIVE_RULE_CITATION_MISSING")
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
        if any(
            contains_normalized_statement(output.answer, notice["summary"])
            for notice in validated_inputs["notices"]
        ):
            raise OutputValidationError("NOTICE_CONTENT_IN_ANSWER")
        if output.evidence_sufficiency == "insufficient" or not all_claims_have_direct_support(
            output.answer,
            [citation.model_dump(mode="json") for citation in output.citations],
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
