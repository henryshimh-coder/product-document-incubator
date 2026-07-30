from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator

from src.infrastructure.gateways.dify_client import DifyClient
from src.infrastructure.gateways.ingest_gateway import IngestGateway
from src.infrastructure.gateways.lint_gateway import LintGateway
from src.infrastructure.gateways.query_gateway import QueryGateway


class DifyGatewaySettings(BaseModel):
    """Secret-safe settings for the three independently governed workflows."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: HttpUrl
    ingest_api_key: SecretStr = Field(min_length=1)
    query_api_key: SecretStr = Field(min_length=1)
    lint_api_key: SecretStr = Field(min_length=1)

    @model_validator(mode="after")
    def require_distinct_api_keys(self) -> Self:
        keys = {
            self.ingest_api_key.get_secret_value(),
            self.query_api_key.get_secret_value(),
            self.lint_api_key.get_secret_value(),
        }
        if len(keys) != 3:
            raise ValueError("Dify workflow API keys must be distinct")
        return self


@dataclass(frozen=True)
class WorkflowGateways:
    ingest: IngestGateway
    query: QueryGateway
    lint: LintGateway


def build_workflow_gateways(
    settings: DifyGatewaySettings,
    *,
    http_factory: Callable[[], httpx.Client] = httpx.Client,
) -> WorkflowGateways:
    """Compose one isolated HTTP and Dify client per workflow."""

    base_url = str(settings.base_url)

    def client(api_key: SecretStr) -> DifyClient:
        return DifyClient(
            base_url=base_url,
            api_key=api_key.get_secret_value(),
            http=http_factory(),
        )

    return WorkflowGateways(
        ingest=IngestGateway(client(settings.ingest_api_key)),
        query=QueryGateway(client(settings.query_api_key)),
        lint=LintGateway(client(settings.lint_api_key)),
    )
