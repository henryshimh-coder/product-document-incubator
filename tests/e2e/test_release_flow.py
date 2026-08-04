from __future__ import annotations

from streamlit.testing.v1 import AppTest

from src.domain.enums import ChangeStatus
from src.domain.errors import DomainError, ErrorCode
from tests.integration.release_env import TARGET_VERSION, make_change


def _render(
    changes,
    *,
    blocked=False,
    review_error=None,
    publish_error=None,
    services=True,
) -> None:
    import streamlit as st

    from src.application.container import AppContainer, AppSettings
    from src.domain.enums import BaselineStatus, ChangeStatus
    from src.domain.models import Baseline, RepairResult
    from src.infrastructure.recovery.release_guard import ReleaseGuard
    from src.ui.pages import release as release_page
    from src.ui.theme.loader import load_theme
    from tests.integration.release_env import NOW, TARGET_BASELINE_ID, TARGET_VERSION, make_change

    def published_baseline() -> Baseline:
        return Baseline(
            id=TARGET_BASELINE_ID,
            project_id="LLD",
            version=TARGET_VERSION,
            parent_baseline_id="BASE-LLD-724_1",
            status=BaselineStatus.EFFECTIVE,
            full_document_path=(
                f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/full.md"
            ),
            card_snapshot_path=(
                f"data/obsidian_vault/02_Current_Baseline/{TARGET_VERSION}/cards.json"
            ),
            manifest_sha256="d" * 64,
            change_request_id="CHANGE-001",
            approved_by="产品经理",
            effective_at=NOW,
            created_at=NOW,
        )

    class CandidateService:
        def list_release_candidates(self, project_id):
            return list(changes)

    class ReviewService:
        def execute(self, command):
            st.session_state.setdefault("release_test_review_calls", []).append(
                command.model_dump(mode="json")
            )
            if review_error is not None:
                raise review_error
            return make_change(ChangeStatus.APPROVED, idempotency_key=command.idempotency_key)

    class PublishService:
        def execute(self, command):
            st.session_state.setdefault("release_test_publish_calls", []).append(
                command.model_dump(mode="json")
            )
            if publish_error is not None:
                raise publish_error
            return published_baseline()

    class Reconciliation:
        def validate_manifest_mirror(self):
            return RepairResult(success=True, repaired_entities=[], error_code=None)

        def rebuild_current_from_manifest(self):
            return RepairResult(success=True, repaired_entities=[], error_code=None)

    guard = ReleaseGuard()
    if blocked:
        guard.block("manifest_sqlite_mismatch")

    load_theme()
    release_page.render(
        AppContainer(
            settings=AppSettings(
                name="产品智策",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
                lint_input_contract_version="2.0",
            ),
            release_candidates=CandidateService() if services else None,
            review_change_request=ReviewService() if services else None,
            publish_baseline=PublishService() if services else None,
            release_guard=guard,
            reconciliation=Reconciliation(),
        )
    )


def _html(page: AppTest) -> str:
    return "\n".join(item.value for item in page.markdown)


def test_release_page_shows_candidates_detail_and_single_primary_action() -> None:
    """Catches the release workspace losing its hierarchy or exposing multiple main actions."""
    page = AppTest.from_function(
        _render,
        args=(
            [
                make_change(ChangeStatus.APPROVED),
                make_change(ChangeStatus.PENDING_APPROVAL, change_id="CHANGE-002"),
                make_change(ChangeStatus.NEEDS_INFO, change_id="CHANGE-003"),
            ],
        ),
    ).run()

    assert not page.exception
    rendered = _html(page)
    assert 'data-layout="38-62"' in rendered
    radio = page.radio(key="release_candidate")
    assert len(radio.options) == 3
    assert radio.options[0].startswith("已批准·待发布")
    assert radio.options[1].startswith("待审批")
    assert radio.options[2].startswith("退回补充")
    assert "RULE-001" in radio.options[0]
    assert "LLD-724_2" in radio.options[0]
    headings = (
        "变更详情",
        "目标卡片",
        "修改前",
        "修改后",
        "变更依据",
        "影响对象",
        "正式应批准角色",
        "当前演示确认人",
        "目标版本",
        "人工批准状态",
        "发布操作",
    )
    positions = [rendered.index(heading) for heading in headings]
    assert positions == sorted(positions)
    primary = [button for button in page.button if button.proto.type == "primary"]
    assert len(primary) == 1
    assert primary[0].label == "重新校验并发布"
    assert any("上次发布未完成" in item.value for item in page.info)


def test_approve_and_publish_calls_review_then_publish_after_human_confirm() -> None:
    """Catches publishing without review or without the human confirmation step."""
    page = AppTest.from_function(
        _render, args=([make_change(ChangeStatus.PENDING_APPROVAL)],)
    ).run()

    page.text_area(key="release_comment_CHANGE-001").input(
        "已检查修改前后、依据、影响对象和目标版本。"
    )
    page.checkbox(key="release_checked_CHANGE-001").check()
    page.text_area(key="release_note_CHANGE-001").input(
        "完成客群规则调整，保留版本差异与追溯依据。"
    )
    page.button(key="release_publish_CHANGE-001").click().run()

    assert not page.exception
    rendered = _html(page)
    assert "即将发布新产品基线" in rendered
    confirm = page.button(key="release_confirm_yes")
    assert confirm.label == "确认发布新基线"
    assert "release_test_review_calls" not in page.session_state
    confirm.click().run()

    assert not page.exception
    reviews = page.session_state["release_test_review_calls"]
    publishes = page.session_state["release_test_publish_calls"]
    assert len(reviews) == 1
    assert reviews[0]["action"] == "approve"
    assert len(publishes) == 1
    assert publishes[0]["approved_by"] == "产品经理"
    assert publishes[0]["impact_reviewed"] is True
    assert any("新基线已发布并生效" in item.value for item in page.success)
    rendered = _html(page)
    assert TARGET_VERSION in rendered
    assert "LLD-724_1" in rendered
    assert "差异摘要" in rendered


def test_approved_retry_calls_publish_only_without_second_review() -> None:
    """Catches a publish retry writing a duplicate approval record."""
    page = AppTest.from_function(_render, args=([make_change(ChangeStatus.APPROVED)],)).run()

    page.checkbox(key="release_checked_CHANGE-001").check()
    page.text_area(key="release_note_CHANGE-001").input(
        "重新校验后重试发布，保留版本差异与追溯依据。"
    )
    page.button(key="release_publish_CHANGE-001").click().run()
    page.button(key="release_confirm_yes").click().run()

    assert not page.exception
    assert "release_test_review_calls" not in page.session_state
    publishes = page.session_state["release_test_publish_calls"]
    assert len(publishes) == 1
    assert publishes[0]["approved_by"] == "产品经理"


def test_publish_requires_checkbox_and_release_note() -> None:
    """Catches the pre-publish form bypassing the human impact confirmation."""
    page = AppTest.from_function(
        _render, args=([make_change(ChangeStatus.PENDING_APPROVAL)],)
    ).run()

    page.text_area(key="release_comment_CHANGE-001").input(
        "已检查修改前后、依据、影响对象和目标版本。"
    )
    page.button(key="release_publish_CHANGE-001").click().run()

    assert not page.exception
    assert any("我已检查修改前后、依据和影响" in item.value for item in page.warning)
    assert "release_test_publish_calls" not in page.session_state


def test_secondary_review_action_rejects_with_comment() -> None:
    """Catches secondary actions silently publishing or skipping the review service."""
    page = AppTest.from_function(
        _render, args=([make_change(ChangeStatus.PENDING_APPROVAL)],)
    ).run()

    page.text_area(key="release_comment_CHANGE-001").input("依据不足，本次驳回该变更。")
    page.button(key="release_reject_CHANGE-001").click().run()

    assert not page.exception
    reviews = page.session_state["release_test_review_calls"]
    assert len(reviews) == 1
    assert reviews[0]["action"] == "reject"
    assert "release_test_publish_calls" not in page.session_state
    assert any("已驳回" in item.value for item in page.success)


def test_needs_info_change_is_read_only() -> None:
    """Catches a returned change bypassing the state machine to be approved again."""
    page = AppTest.from_function(_render, args=([make_change(ChangeStatus.NEEDS_INFO)],)).run()

    assert not page.exception
    assert not [button for button in page.button if button.proto.type == "primary"]
    assert any("已退回补充" in item.value for item in page.info)


def test_blocked_guard_disables_publish_actions() -> None:
    """Catches publish actions staying available while the mirror is inconsistent."""
    page = AppTest.from_function(
        _render,
        args=([make_change(ChangeStatus.PENDING_APPROVAL)],),
        kwargs={"blocked": True},
    ).run()

    assert not page.exception
    assert any("发布已暂停" in item.value for item in page.error)
    assert any("已禁用" in item.value for item in page.warning)
    assert not [button for button in page.button if button.proto.type == "primary"]


def test_publish_failure_shows_banner_step_code_and_old_version_alive() -> None:
    """Catches a failed publish hiding the failure step or the surviving version."""
    page = AppTest.from_function(
        _render,
        args=([make_change(ChangeStatus.APPROVED)],),
        kwargs={"publish_error": DomainError(ErrorCode.RELEASE_FAILED, "COMMIT_FAILED")},
    ).run()

    page.checkbox(key="release_checked_CHANGE-001").check()
    page.text_area(key="release_note_CHANGE-001").input(
        "重新校验后重试发布，保留版本差异与追溯依据。"
    )
    page.button(key="release_publish_CHANGE-001").click().run()
    page.button(key="release_confirm_yes").click().run()

    assert not page.exception
    errors = [item.value for item in page.error]
    assert any("发布未完成" in value and "原子发布" in value for value in errors)
    assert any("RELEASE_FAILED" in value for value in errors)
    assert any("原版本仍然生效" in item.value for item in page.info)


def test_mirror_repair_failure_shows_accurate_status_and_recheck() -> None:
    """Catches a mirror repair failure claiming the old version is still effective."""
    page = AppTest.from_function(
        _render,
        args=([make_change(ChangeStatus.APPROVED)],),
        kwargs={"publish_error": DomainError(ErrorCode.RELEASE_MIRROR_REPAIR_REQUIRED)},
    ).run()

    page.checkbox(key="release_checked_CHANGE-001").check()
    page.text_area(key="release_note_CHANGE-001").input(
        "重新校验后重试发布，保留版本差异与追溯依据。"
    )
    page.button(key="release_publish_CHANGE-001").click().run()
    page.button(key="release_confirm_yes").click().run()

    assert not page.exception
    assert any("新版本已生效" in item.value for item in page.warning)
    page.button(key="release_recheck_failure").click().run()
    assert not page.exception
    assert any("发布已恢复" in item.value for item in page.success)


def test_release_page_without_services_degrades_without_exception() -> None:
    """Catches an uninitialized workspace turning the release page into an exception."""
    page = AppTest.from_function(_render, args=([],), kwargs={"services": False}).run()

    assert not page.exception
    assert any("发布服务尚未就绪" in item.value for item in page.info)
