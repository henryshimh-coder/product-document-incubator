# Product Document Incubator 2.3 验收记录

> 状态：**通过，待 Owner 决定合并/发布**
> 结论：Task 5 聚焦、2.3 聚焦和新鲜全量回归均通过；控制器批准的测试专用临时库隔离修复使全部测试只使用各自临时目录，全量结果为 1155 passed。最终 UI 截图已用临时本机项目库生成并完成目视核验；提交 SHA 由本 Task 的精确提交记录。

## 版本与证据口径

- 提交基线 HEAD：`40631288422ea87840a6a9862ea65c7de468fbd9`；Task 5 提交 SHA 见最终交接记录。
- 证据时间：2026-08-25（America/Los_Angeles）。
- 下表只引用本轮新鲜执行的命令与断言；不用历史报告的通过数外推。

## AC-01～AC-20

| AC | 结果 | 可复验证据 |
| --- | --- | --- |
| AC-01 Owner 授权 | PASS | `test_missing_owner_authorization_never_reaches_gateway` 五组参数覆盖请求者、材料两个授权标记和 L3/L4，断言 Fake Gateway `calls == []`；Owner+L2 成功用例有 1 次调用。 |
| AC-02 业务词放行 | PASS | `test_owner_confirmed_l2_business_terms_reach_gateway_with_hard_ids_masked` 断言“某银行”和“灰度策略”原文进入 Fake Gateway。 |
| AC-03 硬标识遮盖 | PASS（组合证据） | Owner Fake Gateway 用例断言手机/邮箱明文不在载荷，只有 `[已脱敏:phone]` / `[已脱敏:email]`；`test_redacts_supported_identifiers_deterministically` 覆盖手机、身份证、银行卡、邮箱四种占位符；Gateway residue 参数测试分别在外调前拒绝四种明文。 |
| AC-04 Raw 不变 | PASS | Owner 成功 Ingest 用例保存并比对 Ingest 前后 Raw bytes 与 SHA-256；删除用例额外断言回收区中原始 bytes 与入库 SHA-256 一致。 |
| AC-05 比例口径 | PASS | `test_wiki_coverage_counts_only_source_chunk_text` 证明分子只计 `source_chunks[*].text`；成功审计用例按 `source_chunk_chars / source_total_chars` 重算并用 `pytest.approx` 对比入库值。 |
| AC-06 25% 上限 | PASS | `test_wiki_ingest_reports_coverage_error_before_gateway` 断言 `OUTBOUND_COVERAGE_EXCEEDED` 且 Fake Gateway 调用数为 0。 |
| AC-07 20,000 字符上限 | PASS | `test_proof_factory_rejects_payload_above_absolute_canonical_ceiling` 在本地证明工厂拒绝超大规范化完整载荷。 |
| AC-08 未授权零外发 | PASS | Owner 授权参数测试对 Agent、未确认脱敏、未允许外调均断言 `calls == []`；审计失败测试不保存正文。 |
| AC-09 L3/L4 零外发 | PASS | 同一参数测试的 L3/L4 两组断言 Fake Gateway `calls == []`；`test_local_l3_l4_ingest_use_cases_have_no_gateway_dependency` 证明本地路径无 Gateway 依赖。 |
| AC-10 证明不可伪造 | PASS | `test_gateway_rejects_proof_bound_to_an_earlier_payload_before_external_call`、`test_gateway_rejects_tampered_or_fake_signed_proof` 五组字段及 `test_gateway_rejects_coverage_mode_tamper_when_both_modes_are_valid` 均在外调前拒绝。 |
| AC-11 页面告知 | PASS | `test_materials_page_explains_owner_outbound_authorization` 与 `test_authorized_archived_material_shows_owner_outbound_notice`。 |
| AC-12 错误区分 | PASS | Wiki Ingest 用例分别断言 `WIKI_EXTERNAL_CALL_DENIED`、`OUTBOUND_COVERAGE_EXCEEDED`、`REDACTION_REQUIRED`；249 项 2.3 聚焦套件通过。 |
| AC-13 审计 | PASS | `test_ingest_archived_l2_source_updates_complete_wiki` 断言 `authorized=1`、`redacted=1`、`result_mode=realtime`、真实 `outbound_chars`与重算 `outbound_coverage`；日志不保存正文。 |
| AC-14 严格路径兼容 | PASS（聚焦） | Task 3 原样关联回归 242 passed，包含 Gateway schema、Wiki Gateway、Wiki Ingest、Query 与 `ImportSource`；默认 strict 脱敏单测也通过。 |
| AC-15 Dify 兼容 | PASS | `test_wiki_gateway_sends_only_builder_authorized_projection_with_explicit_timeout` 及 schema/task-version 回归仍使用 2.2 输入输出，本轮无 Schema 变更。 |
| AC-16 完整回归 | PASS | 使用新建临时 `INCUBATOR_LIBRARY_ROOT` 的新鲜全量 `.venv/bin/pytest -q`：1155 passed in 27.97s；未访问或修改 Owner 外部默认项目库。 |
| AC-17 版本归组 | PASS | `test_materials_manager_groups_versions_and_keeps_newest_outside_history` 构造3+1 版本，断言只有2个主组，最新 v3.0 在外，v2.0/v1.0 在历史 expander。 |
| AC-18 搜索与信息层级 | PASS（自动化） | 三组参数化用例分别验证关键字/状态/类型筛选；技术详情用例断言绝对路径、Source ID、SHA-256、Schema 的层级；行内失败与“查看技术错误码”用例通过。 |
| AC-19 可删状态 | PASS | `pending_ingest` / `ingest_failed` 两组删除用例断言 Source 目录进入回收区、manifest 保留元数据与 SHA、DB/index 不再显示；E2E 验证二次确认和下一版本提升。 |
| AC-20 不可删状态 | PASS | `ingesting` / `ingested` 两组服务端禁删断言 `MATERIAL_DELETE_NOT_ALLOWED`，并逐字节对比 DB/index/Raw/Wiki 不变；E2E 断言两者均无删除按钮。 |

## 命令记录

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py tests/e2e/test_materials_page.py -k 'group or filter or detail or delete'` | TDD RED：收集阶段因 `DeleteArchivedSourceInput` 不存在而失败，16 deselected；失败原因与尚未实现功能一致。 |
| `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py` | 9 passed in 0.21s。 |
| `.venv/bin/pytest -q tests/e2e/test_materials_page.py` | 23 passed in 2.23s。 |
| `.venv/bin/pytest -q tests/integration/use_cases/test_delete_archived_source.py tests/integration/files/test_project_source_archive.py tests/e2e/test_materials_page.py` | 37 passed in 2.36s。 |
| 2.3 聚焦套件（brief Step 8 第一条） | 249 passed in 4.99s。 |
| Task 3 严格路径关联回归 | 242 passed in 3.51s。 |
| 六个获批隔离修复文件的聚焦回归 | 68 passed in 1.95s。 |
| `INCUBATOR_LIBRARY_ROOT=<fresh temp dir> .venv/bin/pytest -q` | PASS；1155 passed in 27.97s。 |
| brief 规定 Ruff check | PASS，`All checks passed!`。 |
| brief 规定 Ruff format check | PASS，`18 files already formatted`。 |
| 测试隔离修复 Ruff check / format check | PASS，`All checks passed!` / `7 files already formatted`。 |
| `git diff --check` | PASS，无输出。 |

## 删除事务与 Raw SHA 证据

- 顺序为：规范化并校验 `raw/<year>/<source_id>/` 及原始 SHA → 完整 Source 目录 `os.replace` 进回收区并原子写 manifest → `SourceIndexStore.remove` 原子替换活动索引 → 最后删除中央 DB 记录。
- move/index/DB 三种注入失败均断言 DB 记录、活动索引字节、Raw 字节恢复，回收区无残留事务目录。
- 可删状态中，回收文件 `read_bytes()` 等于删除前 Raw，重算 SHA-256 等于 `SourceRecord.sha256`。

## UI 证据说明

- 本轮最新页面行为证据是 23 个 Streamlit AppTest，其中 Task 5 覆盖系列归组、三种筛选、技术详情、行内错误、禁删、二次确认和版本提升。
- `docs/qa/ux-audit/2.3-archived-materials/01-current-archived-materials.png` 是 Task 5 开始前已存在的 **Owner 未跟踪证据**；本轮未修改、未暂存，也不将它声称为 Task 5 最终 UI 截图。
- 新鲜截图：[product-document-incubator-2.3-materials.png](product-document-incubator-2.3-materials.png)。使用隔离的 `PROJECT_A` 演示库在 1440×1024 本机无头 Chrome 中取得；显示“已归档材料”标签的关键字/状态/类型筛选、系列“路线图”的最新 v3.0、待 Ingest 删除入口，以及“用户调研”的已 Ingest 禁删提示。

## 交接说明

1. 控制器批准后，E2E fixture 以及 18 个受影响的 integration/security/unit 测试设置路径均显式使用各自 `tmp_path`/demo `root` 作为 `INCUBATOR_LIBRARY_ROOT`。六个受影响文件的聚焦回归为 68 passed，全量新鲜回归为 1155 passed；未触碰 Owner 外部默认项目库。
2. Task 5 将以精确暂存方式提交；不得纳入 Owner 的上传安全、上传 UI、旧 UX 图片或规格/计划文件。
