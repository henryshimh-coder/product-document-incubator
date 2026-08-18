from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.domain.enums import SecurityLevel
from src.domain.errors import DomainError, GatewayError
from src.domain.models import SourceRecord
from src.infrastructure.db.connection import connect
from src.infrastructure.files.extractor import extract_document_bytes
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.redactor import redact_text

_CITATION_TOKEN = re.compile(r"【(?P<content>[^【】\r\n]*)】")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_CANONICAL_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_AUTHORIZATION_KEY = secrets.token_bytes(32)
_AUTHORIZATION_ISSUER = object()
_AUTHORIZATION_DETAIL = "WIKI_OUTBOUND_AUTHORIZATION_INVALID"


class _SourceReading(Protocol):
    def get(self, source_id: str) -> SourceRecord: ...


class SafeWikiTopicInput(BaseModel):
    """The only topic representation permitted to cross the model boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=256)
    markdown: str = Field(min_length=1, max_length=10_000)
    source_ids: list[str] = Field(min_length=1, max_length=50)


class WikiOutboundProjection(BaseModel):
    """Safe context plus local-only signals; excluded page metadata is never retained."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    safe_index_projection: str = Field(max_length=5_000)
    safe_related_topics: list[SafeWikiTopicInput] = Field(max_length=20)
    local_sensitive_comparison_required: bool
    excluded_topic_count: int = Field(ge=0)


class WikiOutboundAuthorization:
    """Opaque, process-local attestation over builder-approved Wiki workflow inputs."""

    __slots__ = ("_payload_digest", "_revalidate", "_signature")

    def __init__(
        self,
        issuer: object,
        *,
        payload_digest: bytes,
        signature: bytes,
        revalidate: Callable[[Mapping[str, Any]], None],
    ) -> None:
        if issuer is not _AUTHORIZATION_ISSUER:
            raise TypeError("WikiOutboundAuthorization must be created by the context builder")
        object.__setattr__(self, "_payload_digest", payload_digest)
        object.__setattr__(self, "_signature", signature)
        object.__setattr__(self, "_revalidate", revalidate)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("WikiOutboundAuthorization is immutable")

    def __repr__(self) -> str:
        return "WikiOutboundAuthorization(<opaque>)"


def _authorization_invalid() -> GatewayError:
    return GatewayError.workflow_input_invalid(_AUTHORIZATION_DETAIL)


def _canonical_workflow_inputs(inputs: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    from src.infrastructure.gateways.schemas import WikiIngestWorkflowInput

    try:
        serialized = WikiIngestWorkflowInput.model_validate(inputs).model_dump(mode="json")
    except ValidationError:
        raise _authorization_invalid() from None
    canonical = json.dumps(
        serialized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return serialized, hashlib.sha256(canonical).digest()


def _sign_authorization(
    payload_digest: bytes,
    revalidate: Callable[[Mapping[str, Any]], None],
) -> bytes:
    return hmac.digest(
        _AUTHORIZATION_KEY,
        b"wiki-outbound-authorization-v1\n"
        + payload_digest
        + b"\n"
        + str(id(revalidate)).encode("ascii"),
        "sha256",
    )


def validate_wiki_outbound_authorization(
    inputs: Mapping[str, Any],
    authorization: WikiOutboundAuthorization,
) -> None:
    """Reject any payload not exactly attested by the trusted projection builder."""

    serialized, payload_digest = _canonical_workflow_inputs(inputs)
    if not isinstance(authorization, WikiOutboundAuthorization):
        raise _authorization_invalid()
    try:
        valid = hmac.compare_digest(payload_digest, authorization._payload_digest) and (
            hmac.compare_digest(
                _sign_authorization(payload_digest, authorization._revalidate),
                authorization._signature,
            )
        )
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise _authorization_invalid()
    try:
        authorization._revalidate(serialized)
    except (KeyError, TypeError, ValueError, ValidationError):
        raise _authorization_invalid() from None


class WikiOutboundContextBuilder:
    """Build a fail-closed, source-authorized projection of selected Wiki topics."""

    def __init__(
        self,
        paths: ProjectPaths,
        sources: _SourceReading,
        *,
        db_path: Path | None = None,
        customer_names: Iterable[str] = (),
        strategy_terms: Iterable[str] = (),
        financial_terms: Iterable[str] = (),
        leader_names: Iterable[str] = (),
        unpublished_decisions: Iterable[str] = (),
    ) -> None:
        self.paths = paths
        self.sources = sources
        self.db_path = db_path.resolve() if db_path is not None else None
        self.customer_names = tuple(customer_names)
        self.strategy_terms = tuple(strategy_terms)
        self.financial_terms = tuple(financial_terms)
        self.leader_names = tuple(leader_names)
        self.unpublished_decisions = tuple(unpublished_decisions)
        self.project_root = paths.project_root.resolve()
        self.topics_root = (paths.wiki_root / "topics").resolve()

    def build(
        self,
        project_id: str,
        related_topic_paths: Sequence[str],
    ) -> WikiOutboundProjection:
        if project_id != self.paths.project_id:
            raise ValueError("WIKI_OUTBOUND_PROJECT_MISMATCH")
        if isinstance(related_topic_paths, (str, bytes)):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        paths = tuple(related_topic_paths)
        if len(paths) != len(set(paths)) or len(paths) > 20:
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")

        project_allowed = self._project_allows_external_model(project_id)
        safe_topics: list[SafeWikiTopicInput] = []
        excluded_count = 0
        for relative_path in paths:
            topic_path = self._topic_path(relative_path)
            topic = self._safe_topic(topic_path, project_id, project_allowed)
            if topic is None:
                excluded_count += 1
            else:
                safe_topics.append(topic)

        index_projection = "\n".join(
            f"- {topic.title} [{', '.join(topic.source_ids)}]" for topic in safe_topics
        )
        return WikiOutboundProjection(
            safe_index_projection=index_projection,
            safe_related_topics=safe_topics,
            local_sensitive_comparison_required=excluded_count > 0,
            excluded_topic_count=excluded_count,
        )

    def authorize(
        self,
        inputs: Mapping[str, Any],
        *,
        related_topic_paths: Sequence[str],
    ) -> WikiOutboundAuthorization:
        """Attest exact inputs only after trusted project, source, and projection checks."""

        serialized, payload_digest = _canonical_workflow_inputs(inputs)
        paths = tuple(related_topic_paths)
        self._validate_authorizable(serialized, paths)

        def revalidate(current_inputs: Mapping[str, Any]) -> None:
            self._validate_authorizable(current_inputs, paths)

        return WikiOutboundAuthorization(
            _AUTHORIZATION_ISSUER,
            payload_digest=payload_digest,
            signature=_sign_authorization(payload_digest, revalidate),
            revalidate=revalidate,
        )

    def _validate_authorizable(
        self,
        serialized: Mapping[str, Any],
        related_topic_paths: Sequence[str],
    ) -> None:
        project_id = serialized["project_id"]
        if project_id != self.paths.project_id or not self._project_allows_external_model(
            project_id
        ):
            raise _authorization_invalid()

        expected_projection = self.build(project_id, related_topic_paths)
        expected_topics = [
            topic.model_dump(mode="json") for topic in expected_projection.safe_related_topics
        ]
        if (
            serialized["safe_index_projection"]
            != expected_projection.safe_index_projection
            or serialized["safe_related_topics"] != expected_topics
        ):
            raise _authorization_invalid()

        source_input = serialized["source"]
        try:
            source = self.sources.get(source_input["id"])
        except (KeyError, TypeError, ValueError):
            raise _authorization_invalid() from None
        if not self._source_record_is_exportable(source, project_id):
            raise _authorization_invalid()
        expected_source = {
            "id": source.id,
            "source_type": source.source_type,
            "material_name": source.material_name or Path(source.original_filename).stem,
            "document_version": source.document_version,
            "document_date": source.document_date.isoformat(),
            "applicable_scope": source.applicable_baseline_version,
            "authority_level": source.authority_level.value,
            "security_level": source.security_level.value,
        }
        if source_input != expected_source:
            raise _authorization_invalid()

        trusted_chunks = self._trusted_source_chunks(source)
        chunk_ids = [chunk["chunk_id"] for chunk in serialized["source_chunks"]]
        if len(chunk_ids) != len(set(chunk_ids)) or any(
            trusted_chunks.get(chunk["chunk_id"]) != chunk
            for chunk in serialized["source_chunks"]
        ):
            raise _authorization_invalid()
        if serialized["ingest_contract"] != self._trusted_ingest_contract():
            raise _authorization_invalid()

    def _trusted_source_chunks(self, source: SourceRecord) -> dict[str, dict[str, str]]:
        archive_path = Path(source.archive_path)
        if (
            "\\" in source.archive_path
            or archive_path.as_posix() != source.archive_path
            or any(part in {"", ".", ".."} for part in archive_path.parts)
        ):
            raise _authorization_invalid()
        project_root = self.paths.project_root
        raw_root = self.paths.raw_root
        if (
            project_root.is_symlink()
            or project_root.resolve() != project_root
            or raw_root.is_symlink()
            or raw_root.resolve() != raw_root
            or not raw_root.is_relative_to(project_root)
        ):
            raise _authorization_invalid()
        if archive_path.is_absolute():
            lexical_path = archive_path
        else:
            if not source.archive_path.startswith("raw/"):
                raise _authorization_invalid()
            lexical_path = project_root / archive_path
        resolved_path = lexical_path.resolve()
        if (
            lexical_path.is_symlink()
            or resolved_path != lexical_path
            or not resolved_path.is_relative_to(raw_root)
            or not resolved_path.is_file()
        ):
            raise _authorization_invalid()
        try:
            payload = resolved_path.read_bytes()
        except OSError:
            raise _authorization_invalid() from None
        if hashlib.sha256(payload).hexdigest() != source.sha256:
            raise _authorization_invalid()
        try:
            document = extract_document_bytes(
                payload,
                filename=source.original_filename,
                source_id=source.id,
            )
        except DomainError:
            raise _authorization_invalid() from None
        return {
            chunk.chunk_id: {
                "chunk_id": chunk.chunk_id,
                "locator": chunk.locator,
                "text": chunk.text,
            }
            for chunk in document.chunks
        }

    def _trusted_ingest_contract(self) -> str:
        project_root = self.paths.project_root
        lexical_schema_root = project_root / "schema"
        lexical_path = lexical_schema_root / "ingest-contract.md"
        resolved_path = lexical_path.resolve()
        if (
            project_root.is_symlink()
            or project_root.resolve() != project_root
            or self.paths.schema_root != lexical_schema_root
            or lexical_schema_root.is_symlink()
            or lexical_schema_root.resolve() != lexical_schema_root
            or lexical_path.is_symlink()
            or resolved_path != lexical_path
            or not resolved_path.is_relative_to(project_root)
            or not resolved_path.is_file()
        ):
            raise _authorization_invalid()
        try:
            return resolved_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise _authorization_invalid() from None

    def _project_allows_external_model(self, project_id: str) -> bool:
        # In a composed application the central SQLite registration is the only
        # authority.  project.json is scaffolding metadata, never an elevation
        # path for a project whose permission has been revoked centrally.
        if self.db_path is not None:
            try:
                with connect(self.db_path) as connection:
                    row = connection.execute(
                        "SELECT allow_external_model FROM projects WHERE id = ?",
                        (project_id,),
                    ).fetchone()
            except OSError:
                return False
            return row is not None and bool(row["allow_external_model"])
        project_path = self.paths.system_root / "project.json"
        try:
            payload = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return bool(
            isinstance(payload, dict)
            and payload.get("project_id") == project_id
            and payload.get("allow_external_model") is True
        )

    def _topic_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        path = Path(relative_path)
        if (
            path.is_absolute()
            or "\\" in relative_path
            or path.as_posix() != relative_path
            or not relative_path.startswith("wiki/topics/")
            or not relative_path.endswith(".md")
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        lexical_target = self.paths.project_root / relative_path
        target = lexical_target.resolve()
        if (
            lexical_target.is_symlink()
            or not target.is_relative_to(self.project_root)
            or not target.is_relative_to(self.topics_root)
            or not target.is_file()
        ):
            raise ValueError("WIKI_OUTBOUND_TOPIC_PATH_INVALID")
        return target

    def _safe_topic(
        self,
        path: Path,
        project_id: str,
        project_allowed: bool,
    ) -> SafeWikiTopicInput | None:
        if not project_allowed:
            return None
        try:
            text = path.read_text(encoding="utf-8")
            frontmatter, body = self._parse_topic(text)
        except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError):
            return None
        if frontmatter.get("project_id") != project_id:
            return None
        title = frontmatter.get("topic_id")
        if not isinstance(title, str) or not title.strip():
            return None

        statements: list[str] = []
        source_ids: list[str] = []
        for line in body.splitlines():
            matches = list(_CITATION_TOKEN.finditer(line))
            line_without_complete_tokens = _CITATION_TOKEN.sub("", line)
            if "【" in line_without_complete_tokens or "】" in line_without_complete_tokens:
                return None
            if not matches:
                continue
            resolved_sources = [
                self._resolve_citation(match.group("content")) for match in matches
            ]
            if any(source is None for source in resolved_sources):
                return None
            statement = _BULLET.sub("", _CITATION_TOKEN.sub("", line)).strip()
            if not statement or statement.startswith("#"):
                return None
            redaction = redact_text(
                statement,
                security_level=SecurityLevel.L2_INTERNAL,
                customer_names=self.customer_names,
                strategy_terms=self.strategy_terms,
                financial_terms=self.financial_terms,
                leader_names=self.leader_names,
                unpublished_decisions=self.unpublished_decisions,
            )
            if not redaction.safe_for_external_model or redaction.redacted_text != statement:
                return None
            statements.append(statement)
            for source in resolved_sources:
                assert source is not None
                if not self._source_record_is_exportable(source, project_id):
                    return None
                source_id = source.id
                if source_id not in source_ids:
                    source_ids.append(source_id)
        if not statements or not source_ids:
            return None
        return SafeWikiTopicInput(
            title=title,
            markdown="\n".join(statements),
            source_ids=source_ids,
        )

    def _resolve_citation(self, content: str) -> SourceRecord | None:
        separators = [index for index, character in enumerate(content) if character in {":", "："}]
        if not separators:
            return None
        # Source IDs are canonical ASCII identifiers.  Splitting at the first
        # separator preserves normal locators such as "heading:目标; line:1"
        # while refusing an old/forged colon-bearing ID as an external citation.
        index = separators[0]
        source_id = content[:index].strip()
        locator = content[index + 1 :].strip()
        if (
            not _CANONICAL_SOURCE_ID.fullmatch(source_id)
            or not locator
            # `SRC:L3:section` is ambiguous under legacy colon-bearing IDs.
            # A normal Chinese locator may itself contain `:` (for line/page),
            # but an ASCII source separator followed by another ASCII separator
            # is never an unambiguous canonical citation.
            or (content[index] == ":" and ":" in locator)
        ):
            return None
        try:
            return self.sources.get(source_id)
        except (KeyError, TypeError, ValueError):
            return None

    def safe_source_page(self, source: SourceRecord, markdown: str) -> str | None:
        """Return an unchanged source page only when its whole content is exportable."""

        cited = self.citation_sources(markdown)
        if not cited or not any(item.id == source.id for item in cited):
            return None
        if any(
            not self._source_record_is_exportable(item, source.project_id) for item in cited
        ):
            return None
        redaction = redact_text(
            markdown,
            security_level=source.security_level,
            customer_names=self.customer_names,
            strategy_terms=self.strategy_terms,
            financial_terms=self.financial_terms,
            leader_names=self.leader_names,
            unpublished_decisions=self.unpublished_decisions,
        )
        if not redaction.safe_for_external_model or redaction.redacted_text != markdown:
            return None
        return markdown

    def citation_sources(self, markdown: str) -> list[SourceRecord]:
        """Parse complete citation tokens only; malformed tokens fail closed."""

        sources: list[SourceRecord] = []
        for line in markdown.splitlines():
            matches = list(_CITATION_TOKEN.finditer(line))
            remainder = _CITATION_TOKEN.sub("", line)
            if "【" in remainder or "】" in remainder:
                raise ValueError("WIKI_CITATION_INVALID")
            for match in matches:
                source = self._resolve_citation(match.group("content"))
                if source is None:
                    raise ValueError("WIKI_CITATION_INVALID")
                sources.append(source)
        return sources

    @staticmethod
    def _source_record_is_exportable(source: SourceRecord, project_id: str) -> bool:
        return all(
            (
                source.project_id == project_id,
                source.security_level
                in {SecurityLevel.L1_PUBLIC_SIMULATED, SecurityLevel.L2_INTERNAL},
                source.is_redacted,
                source.allow_external_model,
                not source.is_sandbox
                or source.security_level == SecurityLevel.L1_PUBLIC_SIMULATED,
            )
        )

    @staticmethod
    def _parse_topic(markdown: str) -> tuple[dict[str, object], str]:
        if not markdown.startswith("---\n"):
            raise ValueError("WIKI_OUTBOUND_TOPIC_FRONTMATTER_INVALID")
        closing = markdown.find("\n---\n", len("---\n"))
        if closing < 0:
            raise ValueError("WIKI_OUTBOUND_TOPIC_FRONTMATTER_INVALID")
        frontmatter = yaml.safe_load(markdown[len("---\n") : closing])
        if not isinstance(frontmatter, dict):
            raise ValueError("WIKI_OUTBOUND_TOPIC_FRONTMATTER_INVALID")
        return frontmatter, markdown[closing + len("\n---\n") :]
