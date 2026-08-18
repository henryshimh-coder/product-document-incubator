from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.application.dto.wiki_ingest import (
    ConfirmLocalWikiIngestInput,
    PrepareLocalWikiIngestInput,
)
from src.application.use_cases.confirm_local_wiki_ingest import ConfirmLocalWikiIngest
from src.application.use_cases.prepare_local_wiki_ingest import PrepareLocalWikiIngest
from src.domain.enums import DocumentGenerationMode, SecurityLevel
from src.domain.errors import DomainError
from src.domain.wiki import WikiIngestStatus
from src.infrastructure.db.connection import connect
from src.infrastructure.db.repositories import (
    SqliteSourceRepository,
    SqliteWikiIngestRunRepository,
)
from tests.integration.use_cases.test_wiki_ingest import IngestFixture, make_ingest_fixture


@dataclass
class LocalIngestFixture:
    base: IngestFixture
    prepare: PrepareLocalWikiIngest
    confirm: ConfirmLocalWikiIngest

    @property
    def paths(self):
        return self.base.paths

    @property
    def source_id(self) -> str:
        return self.base.source_id

    @property
    def gateway(self):
        return self.base.gateway

    def prepare_draft(self) -> Path:
        result = self.prepare.execute(
            PrepareLocalWikiIngestInput(
                project_id="PROJECT_A", source_id=self.source_id, requested_by="Owner"
            )
        )
        return result.draft_root

    def fill_valid_draft(self) -> Path:
        root = self.prepare_draft()
        source = root / "source.md"
        source.write_text(
            source.read_text(encoding="utf-8")
            .replace(
                "## 来源摘要\n\n\n## 来源定位",
                "## 来源摘要\n\nOwner reviewed the restricted material locally.\n\n## 来源定位",
            ),
            encoding="utf-8",
        )
        topic = root / "topics" / "sensitive-principles.md"
        topic.write_text(
            "---\n"
            "page_type: topic\n"
            "topic_id: sensitive-principles\n"
            "project_id: PROJECT_A\n"
            "---\n"
            "# 主题：敏感产品原则\n\n"
            "## 当前综合结论\n\n"
            "- Owner 已在本地核验限制级材料。 【SRC-PROJECT-A-001：Owner local review】\n\n"
            "## 支持来源\n\n"
            "- 【SRC-PROJECT-A-001：Owner local review】\n\n"
            "## 冲突来源\n\n- 无\n\n"
            "## 待确认项\n\n- 无\n",
            encoding="utf-8",
        )
        return root

    def confirm_source(self):
        return self.confirm.execute(
            ConfirmLocalWikiIngestInput(
                project_id="PROJECT_A", source_id=self.source_id, requested_by="Owner"
            )
        )


def make_local_ingest_fixture(tmp_path: Path) -> LocalIngestFixture:
    base = make_ingest_fixture(tmp_path)
    sources = SqliteSourceRepository(base.db_path)
    source = sources.get(base.source_id).model_copy(
        update={
            "security_level": SecurityLevel.L4_RESTRICTED,
            "is_redacted": False,
            "allow_external_model": False,
        }
    )
    sources.update(source)
    from src.infrastructure.files.source_index_store import SourceIndexStore

    SourceIndexStore(base.paths).upsert(source)
    return LocalIngestFixture(
        base=base,
        prepare=PrepareLocalWikiIngest(
            paths=base.paths,
            sources=sources,
        ),
        confirm=ConfirmLocalWikiIngest(
            paths=base.paths,
            db_path=base.db_path,
            sources=sources,
            runs=SqliteWikiIngestRunRepository(base.db_path),
        ),
    )


@pytest.fixture
def local_ingest_fixture(tmp_path: Path) -> LocalIngestFixture:
    return make_local_ingest_fixture(tmp_path)


def test_prepare_l4_creates_local_template_without_gateway(local_ingest_fixture) -> None:
    """Sensitive Raw must only become an Owner-editable local draft."""
    result = local_ingest_fixture.prepare.execute(
        PrepareLocalWikiIngestInput(
            project_id="PROJECT_A",
            source_id="SRC-PROJECT-A-001",
            requested_by="Owner",
        )
    )

    assert result.status is WikiIngestStatus.LOCAL_REVIEW_REQUIRED
    assert (result.draft_root / "README.md").is_file()
    assert (result.draft_root / "source.md").is_file()
    assert (result.draft_root / "topics").is_dir()
    assert "Approved redacted product principle" not in (
        result.draft_root / "source.md"
    ).read_text(encoding="utf-8")
    assert local_ingest_fixture.gateway.calls == []
    assert SqliteSourceRepository(local_ingest_fixture.base.db_path).get(
        local_ingest_fixture.source_id
    ).ingest_status == WikiIngestStatus.LOCAL_REVIEW_REQUIRED


def test_confirm_local_draft_commits_wiki_and_removes_draft(local_ingest_fixture) -> None:
    """A valid Owner draft must use the shared transaction path without any Gateway."""
    draft_root = local_ingest_fixture.fill_valid_draft()

    result = local_ingest_fixture.confirm_source()

    assert result.status is WikiIngestStatus.INGESTED
    assert result.source_page_path is not None
    assert local_ingest_fixture.base.page(result.source_page_path).is_file()
    assert result.topic_page_paths
    assert all(local_ingest_fixture.base.page(path).is_file() for path in result.topic_page_paths)
    assert not draft_root.exists()
    assert local_ingest_fixture.gateway.calls == []
    persisted = SqliteSourceRepository(local_ingest_fixture.base.db_path).get(
        local_ingest_fixture.source_id
    )
    assert persisted.generation_mode is DocumentGenerationMode.LOCAL_MANUAL
    transaction = next((local_ingest_fixture.paths.system_root / "transactions").iterdir())
    result_json = (transaction / "result.json").read_text(encoding="utf-8")
    assert "Owner reviewed the restricted material" not in result_json
    with connect(local_ingest_fixture.base.db_path) as connection:
        model_calls = connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0]
    assert model_calls == 0


def test_invalid_local_draft_is_preserved_for_owner_correction(local_ingest_fixture) -> None:
    """Validation failure leaves the local draft available for correction."""
    draft_root = local_ingest_fixture.prepare_draft()

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        local_ingest_fixture.confirm_source()

    assert draft_root.is_dir()
    assert local_ingest_fixture.gateway.calls == []


def test_local_ingest_rejects_l1_l2_without_gateway(local_ingest_fixture) -> None:
    """The local route is reserved for sensitive L3/L4 material."""
    sources = SqliteSourceRepository(local_ingest_fixture.base.db_path)
    source = sources.get(local_ingest_fixture.source_id).model_copy(
        update={"security_level": SecurityLevel.L2_INTERNAL}
    )
    sources.update(source)

    with pytest.raises(DomainError, match="WIKI_EXTERNAL_CALL_DENIED"):
        local_ingest_fixture.prepare_draft()

    assert local_ingest_fixture.gateway.calls == []
