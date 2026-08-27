from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, StrictInt, model_validator

from src.infrastructure.gateways.dify_client import DifyClient
from src.infrastructure.gateways.document_gateway import DocumentWorkflowGateway
from src.infrastructure.gateways.ingest_gateway import IngestGateway
from src.infrastructure.gateways.lint_gateway import LintGateway
from src.infrastructure.gateways.query_gateway import QueryGateway
from src.infrastructure.gateways.wiki_ingest_gateway import WikiIngestGateway

MAX_WORKFLOW_TIMEOUT_SECONDS = 600


class WorkflowTimeouts(BaseModel):
    """受治理 Workflow 的显式超时时长（秒）。

    T15-R01：超时必须来自 config/app.yaml 的 timeouts 节点并经严格校验——
    拒绝缺失字段、非整数、零、负值与超过 600 秒的不合理上限，禁止运行时
    回落到网关隐式默认值。
    """

    model_config = ConfigDict(extra="forbid")

    ingest_seconds: StrictInt = Field(gt=0, le=MAX_WORKFLOW_TIMEOUT_SECONDS)
    query_seconds: StrictInt = Field(gt=0, le=MAX_WORKFLOW_TIMEOUT_SECONDS)
    lint_seconds: StrictInt = Field(gt=0, le=MAX_WORKFLOW_TIMEOUT_SECONDS)
    # 2.0 文档工作流是增量能力；保持旧版本地配置可启动，但生产
    # config/app.yaml 显式写入经运行验证的超时值，避免调用端使用隐式 HTTP 超时。
    document_seconds: StrictInt = Field(default=90, gt=0, le=MAX_WORKFLOW_TIMEOUT_SECONDS)


def default_workflow_timeouts() -> WorkflowTimeouts:
    """直接构造（UI 测试等）使用的默认超时；YAML 加载路径不提供默认。"""
    return WorkflowTimeouts(
        ingest_seconds=60,
        query_seconds=30,
        lint_seconds=60,
        document_seconds=90,
    )


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


class DifyDocumentGatewaySettings(BaseModel):
    """独立的产品文档工作流配置，不要求旧版三工作流同时启用。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: HttpUrl
    document_api_key: SecretStr = Field(min_length=1)


class DifyWikiIngestGatewaySettings(BaseModel):
    """Independent Wiki Ingest workflow credentials for the 2.2 boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: HttpUrl
    wiki_ingest_api_key: SecretStr = Field(min_length=1)


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


def build_document_gateway(
    settings: DifyDocumentGatewaySettings,
    *,
    timeouts: WorkflowTimeouts,
    http_factory: Callable[[], httpx.Client] = httpx.Client,
) -> DocumentWorkflowGateway:
    """Compose the 2.0 document workflow without coupling it to legacy flows."""
    return DocumentWorkflowGateway(
        DifyClient(
            base_url=str(settings.base_url),
            api_key=settings.document_api_key.get_secret_value(),
            http=http_factory(),
        ),
        timeout_seconds=timeouts.document_seconds,
    )


def build_wiki_ingest_gateway(
    settings: DifyWikiIngestGatewaySettings,
    *,
    timeouts: WorkflowTimeouts,
    http_factory: Callable[[], httpx.Client] = httpx.Client,
) -> WikiIngestGateway:
    """Compose the dedicated Wiki workflow without coupling legacy gateways."""
    return WikiIngestGateway(
        DifyClient(
            base_url=str(settings.base_url),
            api_key=settings.wiki_ingest_api_key.get_secret_value(),
            http=http_factory(),
        ),
        timeout_seconds=timeouts.ingest_seconds,
    )
