from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.domain.services.citation_validator import CitationValidator
from src.infrastructure.gateways._common import OutboundSafetyProof, invoke, validate_input
from src.infrastructure.gateways.schemas import LintWorkflowInput, LintWorkflowOutput


class LintGateway:
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
            LintWorkflowInput,
            inputs,
            invalid_detail="LINT_INPUT_INVALID",
            safety_proof=safety_proof,
        )
        # 超时一律来自组合根注入的配置（T15-R01）；调用方仅可在测试等
        # 受控场景显式覆盖。
        effective_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        workflow_run_id, raw_output = invoke(self.client, validated_inputs, user, effective_timeout)
        try:
            output = LintWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("LINT_OUTPUT_INVALID") from error
        if output.schema_version != validated_inputs["schema_version"]:
            raise OutputValidationError("SCHEMA_VERSION_MISMATCH")
        allowed_types = set(validated_inputs["allowed_issue_types"])
        if any(issue.issue_type not in allowed_types for issue in output.issues):
            raise OutputValidationError("LINT_OUTPUT_INVALID")

        trusted: dict[str, tuple[Mapping[str, Any], str]] = {}
        for collection_name, expected_side in (
            ("baseline_rules", "current_baseline"),
            ("comparison_items", "challenging_source"),
        ):
            for item in validated_inputs.get(collection_name, []):
                if isinstance(item, Mapping) and item.get("citation_id"):
                    trusted[item["citation_id"]] = (item, expected_side)
        for issue in output.issues:
            for evidence in issue.evidence:
                trusted_evidence = trusted.get(evidence.citation_id)
                if trusted_evidence is None:
                    raise OutputValidationError("UNKNOWN_CITATION")
                source, expected_side = trusted_evidence
                if evidence.side.value != expected_side:
                    raise OutputValidationError("CITATION_SIDE_MISMATCH")
                if (
                    evidence.source_id != source.get("source_id")
                    or evidence.document_version != source.get("document_version")
                    or evidence.page_or_section != source.get("page_or_section")
                ):
                    raise OutputValidationError("CITATION_METADATA_MISMATCH")
                if not CitationValidator([]).has_direct_support(
                    evidence.excerpt,
                    {"excerpt": source.get("excerpt")},
                ):
                    raise OutputValidationError("CITATION_METADATA_MISMATCH")
        return {
            "workflow_run_id": workflow_run_id,
            "result": output.model_dump(mode="json"),
        }
