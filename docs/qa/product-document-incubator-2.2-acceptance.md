# 产品文档孵化器 2.2 验收报告

验收范围：2.2 的项目脚手架、Raw 到 Wiki Ingest、L3/L4 本地确认、Wiki 驱动孵化、跨项目隔离与项目根目录重新定位。自动化命令及结果记录在本文件提交对应的 T12 报告中。

## 质量门禁说明

- 全量测试：`../../.venv/bin/pytest -q`，结果为 `1043 passed`。
- 覆盖率：全量执行结果为 92%，低于原 94% 目标；这是既有覆盖率债务，Owner 已接受，不作为本次 15 人日范围内的阻断项。
- 仓库级 `ruff check .`：仅历史 `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/evidence/t10-t11/joint_acceptance.py` 的 E501/I001 失败；Owner 已接受为既有质量债。
- 仓库级 `ruff format --check .`：报告 31 个既有范围外文件待格式化；Owner 已接受为既有质量债。T12 的四个 Python 验收文件已通过 scoped Ruff 与格式检查。

| AC | 自动化测试节点 | 人工证据路径或动作 | 结果 | Commit SHA |
| --- | --- | --- | --- | --- |
| AC-01 | `test_manage_projects.py::test_scaffolder_builds_complete_2_2_wiki_llm_tree` | 新项目根 `README.md`、`AGENTS.md` | PASS | `3abae54` |
| AC-02 | `test_manage_projects.py::test_create_project_scaffolds_complete_wiki_atomically` | 新项目根 `raw/`、`wiki/`、`schema/`、`exports/`、`.incubator/` | PASS | `3abae54` |
| AC-03 | `test_manage_projects.py::test_scaffolder_builds_complete_2_2_wiki_llm_tree` | 项目根 `README.md` 的“工作流/安全边界” | PASS | `3abae54` |
| AC-04 | `test_manage_projects.py::test_scaffolder_builds_complete_2_2_wiki_llm_tree` | 项目根 `AGENTS.md`、`schema/ingest-contract.md`、`wiki/index.md` | PASS | `3abae54` |
| AC-05 | `test_manage_projects.py::test_create_project_failure_leaves_no_registered_or_visible_project` | 所选父目录及中央 DB `projects` 表 | PASS | `3abae54` |
| AC-06 | `test_archive_raw_source.py::test_archive_marks_new_2_2_project_material_pending_ingest` | `raw/YYYY/SRC-*/`、`.incubator/source-index.json` | PASS | `3abae54` |
| AC-07 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `schema/ingest-contract.md` | PASS | `3abae54` |
| AC-08 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `wiki/sources/*.md` | PASS | `3abae54` |
| AC-09 | `test_wiki_ingest.py::test_existing_topic_update_preserves_prior_evidence_and_appends_conflicts` | `wiki/topics/*.md` | PASS | `3abae54` |
| AC-10 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `wiki/index.md` | PASS | `3abae54` |
| AC-11 | `test_wiki_transaction.py::test_audit_log_renders_one_deterministic_ingest_entry` | `wiki/log.md` | PASS | `3abae54` |
| AC-12 | `test_wiki_ingest.py::test_existing_topic_update_preserves_prior_evidence_and_appends_conflicts` | `wiki/topics/*.md` 的冲突区段 | PASS | `3abae54` |
| AC-13 | `test_wiki_ingest.py::test_successful_duplicate_returns_without_gateway_or_wiki_change` | `wiki/log.md`、`.incubator/transactions/` | PASS | `3abae54` |
| AC-14 | `test_wiki_ingest.py::test_gateway_failure_records_safe_error_and_preserves_wiki` | `wiki/` 提交前后哈希 | PASS | `3abae54` |
| AC-15 | `test_materials_page.py::test_material_page_renders_wiki_ingest_lifecycle` | Streamlit “原始材料”状态与错误码 | PASS | `3abae54` |
| AC-16 | `test_incubate_document.py::test_incubation_lists_only_ingested_sources` | Streamlit “文档孵化”来源清单 | PASS | `3abae54` |
| AC-17 | `test_wiki_incubation_flow.py::test_l2_archive_ingest_wiki_incubate_publish` | `raw/YYYY/SRC-*/requirements.md` SHA-256 | PASS | `3abae54` |
| AC-18 | `test_wiki_incubation_flow.py::test_l4_archive_local_edit_confirm_without_gateway` | `wiki/drafts/local-ingest/`、`model_call_logs` | PASS | `3abae54` |
| AC-19 | `test_wiki_incubation_flow.py::test_two_projects_in_different_roots_complete_isolated_lifecycles` | 两个项目根目录文件树、`wiki_ingest_runs`、`model_call_logs` 与项目级 DB 记录 | PASS | `10a908e` |
| AC-20 | `test_wiki_incubation_flow.py::test_legacy_project_remains_openable_without_automatic_writes` | `legacy/LEGACY_A/` 内容树、`.incubator/project.json` 与项目级 DB；允许唯一空运行锁 `.incubator/locks/wiki-ingest.lock` | PASS | `10a908e` |
| AC-21 | `test_wiki_incubation_flow.py::test_root_readme_navigates_to_ingested_source_and_topic` | `README.md` -> `wiki/index.md` -> `wiki/sources/*.md`、`wiki/topics/*.md` | PASS | `10a908e` |
| AC-22 | `test_wiki_ingest.py::test_ingest_archived_l2_source_updates_complete_wiki` | `wiki/current/`、`wiki/versions/`、`.incubator/current-baseline.json` | PASS | `3abae54` |
| AC-23 | `test_wiki_incubation_flow.py::test_two_projects_in_different_roots_complete_isolated_lifecycles` | `one/PROJECT_A`、`two/PROJECT_B`，两边独立发布及导出 | PASS | `3abae54` |
| AC-24 | `test_manage_projects.py::test_create_registers_owner_selected_root` | 中央 DB `projects.project_root_path` | PASS | `3abae54` |
| AC-25 | `test_wiki_incubation_flow.py::test_project_creation_rejects_invalid_parent_target_and_project_id` | 缺失父目录、已有目标目录与 `../ESCAPE` 输入；不可写目录按下方人工步骤 | PASS | `10a908e` |
| AC-26 | `test_manage_projects.py::test_create_project_rejects_duplicate_id_without_touching_existing_files` | 已有目标目录的文件哈希 | PASS | `3abae54` |
| AC-27 | `test_manage_projects.py::test_create_registers_owner_selected_root` | 中央 DB `projects.project_root_path` 后重启项目中心 | PASS | `3abae54` |
| AC-28 | `test_wiki_incubation_flow.py::test_move_relocate_then_continue_ingest` | 项目中心“路径不可用”与重新定位表单 | PASS | `3abae54` |
| AC-29 | `test_wiki_incubation_flow.py::test_two_projects_in_different_roots_complete_isolated_lifecycles` | A、B 的 `raw/`、`wiki/`、`exports/` 与对应 DB 记录 | PASS | `3abae54` |

### AC-25 不可写父目录人工复核

在 macOS/Linux 创建空目录后执行 `chmod 500 <parent>`，在项目中心选择该目录并创建新项目；应显示 `PROJECT_ROOT_NOT_WRITABLE`，且 `<parent>/<项目ID>` 与中央 DB `projects` 表均无新增。复核后执行 `chmod 700 <parent>` 恢复权限。Windows 使用无写入权限的测试目录执行同一操作。该项受执行账户权限影响，保留为可重复的人工证据，不以平台相关的自动化结果替代。
