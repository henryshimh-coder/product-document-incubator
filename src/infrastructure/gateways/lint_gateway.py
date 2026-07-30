from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.application.ports.workflow_gateway import WorkflowGateway
from src.domain.errors import OutputValidationError
from src.domain.services.citation_validator import CitationValidator
from src.infrastructure.gateways._common import invoke
from src.infrastructure.gateways.schemas import LintWorkflowOutput


class LintGateway:
    def __init__(self, client: WorkflowGateway) -> None:
        self.client = client

    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        user: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        workflow_run_id, raw_output = invoke(self.client, inputs, user, timeout_seconds)
        try:
            output = LintWorkflowOutput.model_validate(raw_output)
        except ValidationError as error:
            raise OutputValidationError("LINT_OUTPUT_INVALID") from error
        if output.schema_version != inputs.get("schema_version"):
            raise OutputValidationError("SCHEMA_VERSION_MISMATCH")
        allowed_types = set(inputs.get("allowed_issue_types", []))
        if any(issue.issue_type not in allowed_types for issue in output.issues):
            raise OutputValidationError("LINT_OUTPUT_INVALID")

        trusted = {}
        for collection_name in ("baseline_rules", "comparison_items", "deterministic_findings"):
            for item in inputs.get(collection_name, []):
                if isinstance(item, Mapping) and item.get("citation_id"):
                    trusted[item["citation_id"]] = item
        for issue in output.issues:
            for evidence in issue.evidence:
                source = trusted.get(evidence.citation_id)
                if source is None:
                    raise OutputValidationError("UNKNOWN_CITATION")
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
