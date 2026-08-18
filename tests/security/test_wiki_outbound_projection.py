from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.models import SourceRecord
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.wiki_outbound_context import WikiOutboundContextBuilder


def test_local_l3_l4_ingest_use_cases_have_no_gateway_dependency() -> None:
    """Sensitive local confirmation must not have a route to construct a model client."""
    from src.application.use_cases.confirm_local_wiki_ingest import ConfirmLocalWikiIngest
    from src.application.use_cases.prepare_local_wiki_ingest import PrepareLocalWikiIngest

    assert "gateway" not in inspect.signature(PrepareLocalWikiIngest).parameters
    assert "gateway" not in inspect.signature(ConfirmLocalWikiIngest).parameters


def _source(
    source_id: str,
    *,
    security_level: SecurityLevel,
    project_id: str = "PROJECT_A",
    is_redacted: bool = True,
    allow_external_model: bool = True,
) -> SourceRecord:
    return SourceRecord(
        id=source_id,
        project_id=project_id,
        original_filename=f"{source_id}.md",
        archive_path=f"raw/{source_id}/{source_id}.md",
        sha256="a" * 64,
        mime_type="text/markdown",
        size_bytes=100,
        source_type="formal_document",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="Product",
        provider=None,
        document_date=date(2026, 8, 17),
        document_version="1.0",
        applicable_baseline_version="BASE-1",
        security_level=security_level,
        is_redacted=is_redacted,
        allow_external_model=allow_external_model,
        is_sandbox=False,
        ingest_status="ingested",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


class _SourceRepository:
    def __init__(self, sources: list[SourceRecord]) -> None:
        self._sources = {source.id: source for source in sources}

    def get(self, source_id: str) -> SourceRecord:
        try:
            return self._sources[source_id]
        except KeyError:
            raise KeyError(f"source not found: {source_id}") from None


@dataclass
class _ProjectWiki:
    paths: ProjectPaths

    def set_project_permission(self, allowed: bool) -> None:
        project_path = self.paths.system_root / "project.json"
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.2",
                    "project_id": self.paths.project_id,
                    "allow_external_model": allowed,
                }
            ),
            encoding="utf-8",
        )

    def write_topic(self, slug: str, *, citations: list[str], body: str) -> str:
        path = self.paths.wiki_root / "topics" / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered_citations = " ".join(f"【{source_id}：section】" for source_id in citations)
        path.write_text(
            "\n".join(
                (
                    "---",
                    "page_type: topic",
                    f'topic_id: "{slug}"',
                    f'project_id: "{self.paths.project_id}"',
                    "---",
                    "",
                    f"# Topic: {slug}",
                    "",
                    "## Confirmed statements",
                    "",
                    f"- {body} {rendered_citations}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        return f"wiki/topics/{slug}.md"


@pytest.fixture
def project_wiki(tmp_path: Path) -> _ProjectWiki:
    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.project_root.mkdir(parents=True)
    wiki = _ProjectWiki(paths)
    wiki.set_project_permission(True)
    paths.wiki_root.mkdir(exist_ok=True)
    (paths.wiki_root / "index.md").write_text(
        "# Full private index\n\nSECRET-INDEX-BODY\n", encoding="utf-8"
    )
    return wiki


@pytest.fixture
def source_repository() -> _SourceRepository:
    return _SourceRepository(
        [
            _source("SRC-L1", security_level=SecurityLevel.L1_PUBLIC_SIMULATED),
            _source("SRC-L2", security_level=SecurityLevel.L2_INTERNAL),
            _source("SRC-L3", security_level=SecurityLevel.L3_CONFIDENTIAL),
            _source("SRC-L4", security_level=SecurityLevel.L4_RESTRICTED),
            _source("SRC.L3", security_level=SecurityLevel.L3_CONFIDENTIAL),
            _source("SRC", security_level=SecurityLevel.L1_PUBLIC_SIMULATED),
            _source("SRC:L3", security_level=SecurityLevel.L3_CONFIDENTIAL),
            _source(
                "SRC-UNREDACTED",
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted=False,
            ),
            _source(
                "SRC-DENIED",
                security_level=SecurityLevel.L2_INTERNAL,
                allow_external_model=False,
            ),
            _source(
                "SRC-OTHER",
                security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
                project_id="PROJECT_B",
            ),
        ]
    )


@pytest.mark.parametrize("restricted_source_id", ["SRC-L3", "SRC-L4", "SRC.L3"])
def test_projection_excludes_entire_topic_with_any_restricted_source(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
    restricted_source_id: str,
) -> None:
    path = project_wiki.write_topic(
        "pricing",
        citations=["SRC-L1", restricted_source_id],
        body="SECRET-PRICE",
    )

    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=[path]
    )

    serialized = projection.model_dump_json()
    assert "SECRET-PRICE" not in serialized
    assert "pricing" not in serialized
    assert path not in serialized
    assert projection.safe_related_topics == []
    assert projection.local_sensitive_comparison_required is True
    assert projection.excluded_topic_count == 1


def test_projection_excludes_whole_topic_for_any_malformed_or_unknown_citation_marker(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
) -> None:
    path = project_wiki.write_topic(
        "mixed-sensitive",
        citations=["SRC-L1"],
        body="SECRET-MIXED 【SRC.L3：section】 【SRC-UNKNOWN without-locator】",
    )

    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=[path]
    )

    serialized = projection.model_dump_json()
    for forbidden in (
        "mixed-sensitive",
        "SECRET-MIXED",
        "SRC.L3",
        "SRC-UNKNOWN",
        path,
    ):
        assert forbidden not in serialized
    assert projection.safe_related_topics == []
    assert projection.local_sensitive_comparison_required is True
    assert projection.excluded_topic_count == 1


def test_projection_resolves_longest_known_colon_source_id_before_authorizing(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
) -> None:
    path = project_wiki.write_topic(
        "colon-ambiguous",
        citations=[],
        body="SECRET-COLON 【SRC:L3:section】",
    )

    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=[path]
    )

    serialized = projection.model_dump_json()
    for forbidden in ("colon-ambiguous", "SECRET-COLON", "SRC:L3", path):
        assert forbidden not in serialized
    assert projection.safe_related_topics == []
    assert projection.local_sensitive_comparison_required is True
    assert projection.excluded_topic_count == 1


def test_projection_rejects_cross_line_citation_token_for_whole_topic(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
) -> None:
    path = project_wiki.write_topic(
        "cross-line",
        citations=["SRC-L1"],
        body="ignored 【SRC-L3\n：section】 SECRET-CROSS-LINE",
    )

    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=[path]
    )

    serialized = projection.model_dump_json()
    for forbidden in ("cross-line", "SECRET-CROSS-LINE", "SRC-L3", path):
        assert forbidden not in serialized
    assert projection.safe_related_topics == []
    assert projection.local_sensitive_comparison_required is True
    assert projection.excluded_topic_count == 1


def test_projection_includes_only_authorized_l1_l2_claims(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
) -> None:
    path = project_wiki.write_topic(
        "channels", citations=["SRC-L1", "SRC-L2"], body="Safe channel"
    )

    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=[path]
    )

    assert projection.safe_related_topics[0].title == "channels"
    assert projection.safe_related_topics[0].markdown == "Safe channel"
    assert projection.safe_related_topics[0].source_ids == ["SRC-L1", "SRC-L2"]
    assert "SECRET-INDEX-BODY" not in projection.safe_index_projection
    assert "wiki/topics" not in projection.model_dump_json()
    assert projection.local_sensitive_comparison_required is False
    assert projection.excluded_topic_count == 0


@pytest.mark.parametrize(
    "source_id",
    ["SRC-UNREDACTED", "SRC-DENIED", "SRC-OTHER", "SRC-UNKNOWN"],
)
def test_projection_excludes_unapproved_or_unresolvable_topic_without_metadata_leak(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
    source_id: str,
) -> None:
    path = project_wiki.write_topic(
        "owner-sensitive-topic", citations=[source_id], body="OWNER-ONLY-CONTENT"
    )

    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=[path]
    )

    serialized = projection.model_dump_json()
    assert "owner-sensitive-topic" not in serialized
    assert "OWNER-ONLY-CONTENT" not in serialized
    assert source_id not in serialized
    assert projection.local_sensitive_comparison_required is True
    assert projection.excluded_topic_count == 1


def test_projection_excludes_every_topic_when_project_disallows_external_model(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
) -> None:
    project_wiki.set_project_permission(False)
    path = project_wiki.write_topic("channels", citations=["SRC-L1"], body="Safe alone")

    projection = WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
        project_id="PROJECT_A", related_topic_paths=[path]
    )

    assert projection.safe_related_topics == []
    assert projection.safe_index_projection == ""
    assert projection.local_sensitive_comparison_required is True
    assert projection.excluded_topic_count == 1


def test_projection_rejects_cross_project_or_non_topic_paths(
    project_wiki: _ProjectWiki,
    source_repository: _SourceRepository,
) -> None:
    project_wiki.write_topic("channels", citations=["SRC-L1"], body="Safe channel")

    with pytest.raises(ValueError, match="WIKI_OUTBOUND_PROJECT_MISMATCH"):
        WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
            project_id="PROJECT_B", related_topic_paths=[]
        )
    with pytest.raises(ValueError, match="WIKI_OUTBOUND_TOPIC_PATH_INVALID"):
        WikiOutboundContextBuilder(project_wiki.paths, source_repository).build(
            project_id="PROJECT_A", related_topic_paths=["wiki/index.md"]
        )
