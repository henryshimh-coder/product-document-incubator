from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, StrictInt, model_validator

from src.infrastructure.gateways.dify_client import DifyClient
from src.infrastructure.gateways.ingest_gateway import IngestGateway
from src.infrastructure.gateways.lint_gateway import LintGateway
from src.infrastructure.gateways.query_gateway import QueryGateway

MAX_WORKFLOW_TIMEOUT_SECONDS = 600


class WorkflowTimeouts(BaseModel):
    """三个受治理 Workflow 的显式超时时长（秒）。

    T15-R01：超时必须来自 config/app.yaml 的 timeouts 节点并经严格校验——
    拒绝缺失字段、非整数、零、负值与超过 600 秒的不合理上限，禁止运行时
    回落到网关隐式默认值。
    """

    model_config = ConfigDict(extra="forbid")

    ingest_seconds: StrictInt = Field(gt=0, le=MAX_WORKFLOW_TIMEOUT_SECONDS)
    query_seconds: StrictInt = Field(gt=0, le=MAX_WORKFLOW_TIMEOUT_SECONDS)
    lint_seconds: StrictInt = Field(gt=0, le=MAX_WORKFLOW_TIMEOUT_SECONDS)


def default_workflow_timeouts() -> WorkflowTimeouts:
    """直接构造（UI 测试等）使用的默认超时；YAML 加载路径不提供默认。"""
    return WorkflowTimeouts(ingest_seconds=60, query_seconds=30, lint_seconds=60)


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
    timeouts: WorkflowTimeouts,
    http_factory: Callable[[], httpx.Client] = httpx.Client,
) -> WorkflowGateways:
    """Compose one isolated HTTP and Dify client per workflow.

    每个网关的超时时长由组合根从已校验配置显式注入（T15-R01），不得依赖
    网关或 HTTP 客户端的隐式默认值。
    """

    base_url = str(settings.base_url)

    def client(api_key: SecretStr) -> DifyClient:
        return DifyClient(
            base_url=base_url,
            api_key=api_key.get_secret_value(),
            http=http_factory(),
        )

    return WorkflowGateways(
        ingest=IngestGateway(
            client(settings.ingest_api_key),
            timeout_seconds=timeouts.ingest_seconds,
        ),
        query=QueryGateway(
            client(settings.query_api_key),
            timeout_seconds=timeouts.query_seconds,
        ),
        lint=LintGateway(
            client(settings.lint_api_key),
            timeout_seconds=timeouts.lint_seconds,
        ),
    )
