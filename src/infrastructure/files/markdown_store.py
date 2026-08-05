from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.domain.errors import DomainError, ErrorCode
from src.domain.models import ChangeRequest, KnowledgeCard
from src.infrastructure.files.manifest_store import fsync_directory, fsync_file

RELEASE_ROOT = Path("data/obsidian_vault/02_Current_Baseline")
QUARANTINE_ROOT = Path("data/obsidian_vault/99_Quarantine")
TEMP_DIR_PREFIX = ".release-tmp-"

_VERSION_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MarkdownStore:
    """Stores readable baseline assets inside the local Obsidian vault."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def write_baseline(
        self, version: str, full_document: str, cards: list[KnowledgeCard]
    ) -> tuple[str, str]:
        validated_cards = [KnowledgeCard.model_validate(card) for card in cards]
        relative_dir = RELEASE_ROOT / version
        target_dir = self.project_root / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        full_path = target_dir / "full.md"
        cards_path = target_dir / "cards.json"
        full_path.write_text(full_document, encoding="utf-8")
        cards_path.write_text(
            json.dumps(
                [card.model_dump(mode="json") for card in validated_cards],
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return (str(relative_dir / "full.md"), str(relative_dir / "cards.json"))

    def read_cards(self, relative_path: str) -> list[KnowledgeCard]:
        payload = json.loads((self.project_root / relative_path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Baseline card snapshot must be a JSON array")
        return [KnowledgeCard.model_validate(card) for card in payload]

    def sha256_for(self, relative_path: str) -> str:
        path = self.project_root / relative_path
        return hashlib.sha256(path.read_bytes()).hexdigest()

    # --- T10 atomic release staging -------------------------------------

    def release_dir_exists(self, version: str) -> bool:
        return self._release_dir(version).exists()

    def create_release_temp_dir(self) -> Path:
        """Create a staging directory on the same filesystem as the release root."""
        release_root = self.project_root / RELEASE_ROOT
        release_root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX, dir=release_root))

    def build_release_full_document(
        self,
        current_full_document_path: str,
        change: ChangeRequest,
        temp_dir: Path,
        *,
        parent_version: str,
    ) -> None:
        """Write the staged full.md replacing the version declaration and one passage."""
        self._require_staging_dir(temp_dir)
        current_text = (self.project_root / current_full_document_path).read_text(encoding="utf-8")
        parent_declaration = f"当前版本：{parent_version}"
        target_declaration = f"当前版本：{change.target_version}"
        declaration_occurrences = current_text.count(parent_declaration)
        if declaration_occurrences != 1:
            raise DomainError(
                ErrorCode.RELEASE_FAILED,
                f"FULL_DOCUMENT_VERSION_DECLARATION_NOT_UNIQUE:{declaration_occurrences}",
            )
        occurrences = current_text.count(change.before_content)
        if occurrences != 1:
            raise DomainError(
                ErrorCode.RELEASE_FAILED,
                f"FULL_DOCUMENT_TARGET_NOT_UNIQUE:{occurrences}",
            )
        new_text = current_text.replace(parent_declaration, target_declaration, 1)
        new_text = new_text.replace(change.before_content, change.after_content, 1)
        if new_text.count(parent_declaration) != 0 or new_text.count(target_declaration) != 1:
            raise DomainError(ErrorCode.RELEASE_FAILED, "FULL_DOCUMENT_VERSION_DECLARATION_INVALID")
        (temp_dir / "full.md").write_text(new_text, encoding="utf-8")

    def build_release_cards(
        self,
        current_card_snapshot_path: str,
        change: ChangeRequest,
        temp_dir: Path,
        *,
        parent_version: str,
        updated_at: datetime,
    ) -> list[KnowledgeCard]:
        """Write the staged cards.json as a complete snapshot of the target version."""
        self._require_staging_dir(temp_dir)
        cards = self.read_cards(current_card_snapshot_path)
        card_ids = [card.id for card in cards]
        if len(set(card_ids)) != len(card_ids):
            raise DomainError(ErrorCode.RELEASE_FAILED, "PARENT_SNAPSHOT_DUPLICATE_CARD_ID")
        mixed = [card.id for card in cards if card.product_version != parent_version]
        if mixed:
            raise DomainError(
                ErrorCode.RELEASE_FAILED,
                f"PARENT_SNAPSHOT_VERSION_MIXED:{','.join(sorted(mixed))}",
            )
        matches = [card for card in cards if card.id == change.target_card_id]
        if len(matches) != 1:
            raise DomainError(
                ErrorCode.RELEASE_FAILED,
                f"TARGET_CARD_NOT_UNIQUE:{len(matches)}",
            )
        target = matches[0]
        if target.content != change.before_content:
            raise DomainError(ErrorCode.RELEASE_FAILED, "BEFORE_CONTENT_MISMATCH")
        new_cards = [
            (
                card.model_copy(
                    update={
                        "content": change.after_content,
                        "product_version": change.target_version,
                        "updated_at": updated_at,
                    }
                )
                if card.id == target.id
                else card.model_copy(update={"product_version": change.target_version})
            )
            for card in cards
        ]
        (temp_dir / "cards.json").write_text(
            json.dumps(
                [card.model_dump(mode="json") for card in new_cards],
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return new_cards

    def write_release_metadata(
        self,
        temp_dir: Path,
        *,
        change: ChangeRequest,
        parent_version: str,
        approved_by: str,
        published_at: datetime,
        release_note: str,
    ) -> None:
        """Write auditable diff.md and release.json next to the staged assets."""
        self._require_staging_dir(temp_dir)
        diff_lines = difflib.unified_diff(
            change.before_content.splitlines(),
            change.after_content.splitlines(),
            fromfile=f"{parent_version}/{change.target_card_id}",
            tofile=f"{change.target_version}/{change.target_card_id}",
            lineterm="",
        )
        diff_document = "\n".join(
            [
                f"# 基线差异 {parent_version} → {change.target_version}",
                "",
                f"- 变更单：{change.id}",
                f"- 目标卡片：{change.target_card_id}",
                f"- 批准人：{approved_by}",
                "",
                "```diff",
                *diff_lines,
                "```",
                "",
            ]
        )
        (temp_dir / "diff.md").write_text(diff_document, encoding="utf-8")
        staged_cards = json.loads((temp_dir / "cards.json").read_text(encoding="utf-8"))
        release_record = {
            "schema_version": "1.0",
            "parent_version": parent_version,
            "target_version": change.target_version,
            "change_request_id": change.id,
            "approved_by": approved_by,
            "published_at": published_at.isoformat(),
            "release_note": release_note,
            "card_count": len(staged_cards),
            "file_sha256": {
                name: hashlib.sha256((temp_dir / name).read_bytes()).hexdigest()
                for name in ("full.md", "cards.json", "diff.md")
            },
        }
        (temp_dir / "release.json").write_text(
            json.dumps(release_record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def commit_release_dir(self, temp_dir: Path, target_version: str) -> Path:
        """Atomically move the staged directory to its final version location."""
        self._require_staging_dir(temp_dir)
        final_dir = self._release_dir(target_version)
        if final_dir.exists():
            raise DomainError(ErrorCode.TARGET_VERSION_ALREADY_EXISTS)
        for name in ("full.md", "cards.json", "diff.md", "release.json"):
            fsync_file(temp_dir / name)
        os.replace(temp_dir, final_dir)
        fsync_directory(final_dir.parent)
        return final_dir

    def discard_temp_dir_if_exists(self, temp_dir: Path | None) -> None:
        if temp_dir is None:
            return
        try:
            self._require_staging_dir(temp_dir)
        except DomainError:
            return
        shutil.rmtree(temp_dir, ignore_errors=True)

    def quarantine_unreferenced_release(self, final_dir: Path) -> None:
        """Move a committed but unreferenced version directory into quarantine."""
        release_root = (self.project_root / RELEASE_ROOT).resolve()
        resolved = Path(final_dir).resolve()
        if resolved.parent != release_root or resolved.name.startswith(TEMP_DIR_PREFIX):
            raise DomainError(ErrorCode.RELEASE_FAILED, "QUARANTINE_SOURCE_INVALID")
        quarantine_root = self.project_root / QUARANTINE_ROOT
        quarantine_root.mkdir(parents=True, exist_ok=True)
        target = quarantine_root / f"{resolved.name}-quarantined-{uuid4().hex[:8]}"
        os.replace(resolved, target)
        fsync_directory(quarantine_root)

    def _release_dir(self, version: str) -> Path:
        if not _VERSION_SAFE.fullmatch(version):
            raise DomainError(ErrorCode.RELEASE_FAILED, "TARGET_VERSION_UNSAFE")
        candidate = (self.project_root / RELEASE_ROOT / version).resolve()
        if candidate.parent != (self.project_root / RELEASE_ROOT).resolve():
            raise DomainError(ErrorCode.RELEASE_FAILED, "TARGET_VERSION_UNSAFE")
        return candidate

    def _require_staging_dir(self, temp_dir: Path) -> None:
        release_root = (self.project_root / RELEASE_ROOT).resolve()
        resolved = Path(temp_dir).resolve()
        if resolved.parent != release_root or not resolved.name.startswith(TEMP_DIR_PREFIX):
            raise DomainError(ErrorCode.RELEASE_FAILED, "STAGING_DIR_INVALID")
