# Task 10 实施报告

## 实现摘要

- 新增 `src/application/dto/release.py`：严格、冻结、`extra='forbid'` 的 `ReviewChangeRequestInput`（复核意见 10–200 字）与 `PublishBaselineInput`（发布说明 20–200 字）。
- 新增 `ReviewChangeRequest` 用例：四种人工复核（approve→approved、reject→rejected、defer→deferred、request_info→needs_info）；先按幂等 key 查询，同 key 同命令返回原结果，同 key 不同命令以 `REVIEW_IDEMPOTENCY_CONFLICT` fail closed；首次复核仅允许 `pending_approval`，否则 `CHANGE_NOT_REVIEWABLE`。
- 新增 `SqliteReviewUnitOfWork`：状态更新与 `change_reviewed` 事件处于同一 `BEGIN IMMEDIATE` 事务，事务内重读状态、复核 key 与状态机迁移防并发复核；SQLite 失败映射 `REVIEW_PERSISTENCE_FAILED` 并保留 sqlite cause；提交后追加 JSONL，追加失败保留 SQLite 事实交给 `EventLogger.reconcile()`。
- 扩展 `MarkdownStore`：同文件系统临时目录、结构化更新 cards.json（精确一张目标卡、再次校验 before_content、版本更新为 target_version）、full.md 唯一精确替换（0 次或多次均 fail closed）、diff.md/release.json 生成、原子 rename 提交（含 fsync）、临时目录安全清理、未引用最终目录隔离至 `99_Quarantine`。版本号与路径均防穿越。
- 扩展 `ManifestStore`：新增 `build_candidate()` 与 `validate_candidate()`（项目/父版本/路径/哈希逐项 fail closed）；保留既有 `atomic_replace()` 及 `ManifestDurabilityUncertainError` 语义。
- 新增 `PublishBaseline` 用例：项目级 filelock（占用即 `RELEASE_LOCKED`，不创建临时目录）→ 读取校验 Manifest 与文件哈希 → `ReleasePolicy` 复核 approved/影响/边界 → 临时目录生成四个文件 → 候选构建与校验 → 原子提交版本目录 → 原子替换 Manifest → SQLite 单事务镜像（旧基线 superseded、新基线 effective、变更 published、项目当前基线、发布事件）→ JSONL 审计。`approved_by` 强制取自人工复核记录的操作员（`APPROVER_MISMATCH` fail closed）。`ManifestDurabilityUncertainError` 后重新读取 Manifest 判定权威状态：已替换则继续、未替换则隔离目录并报 `RELEASE_FAILED`、不可读则不删证据报 `BASELINE_INTEGRITY_FAILED`。
- 新增 `SqliteReleaseUnitOfWork`：单一 `BEGIN IMMEDIATE` 事务完成镜像四步与发布事件；任何一步失败整体回滚并映射稳定错误。
- 新增 `ReconciliationService`：`validate_manifest_mirror()` 校验资产哈希＋Project/Baseline/ChangeRequest 镜像；`rebuild_current_from_manifest()` 以 Manifest 为唯一权威幂等重建（不合成缺失 Project 行、不把 pending 变更伪造成 published、Manifest 无效时绝不反向覆盖）；修复失败由 `ReleaseGuard` 阻断发布（`RELEASE_BLOCKED`），镜像修复失败返回 `RELEASE_MIRROR_REPAIR_REQUIRED`。
- 容器接线：`build_container()` 在迁移后先执行启动对账（不一致→幂等重建→失败则 block），再组装 dashboard/decision/review/publish 等本地服务；`AppContainer` 新增 `review_change_request`、`publish_baseline`、`release_candidates`、`release_guard`、`reconciliation`。
- 发布页：38/62 布局、候选列表（approved 置顶、pending、needs_info 只读）、变更详情固定顺序（目标卡片/修改前后/依据/影响对象/批准角色/演示确认人/目标版本/批准状态）、“批准并发布”唯一主按钮＋驳回/暂缓/退回补充次级操作、勾选与 20–200 字发布说明预检、设计文档原文人工确认弹窗、pending 先 Review 后 Publish、approved 重试只 Publish、成功页（新版本/父版本/发布人/时间/变更单/差异摘要/两个入口）、失败页（红色横幅＋失败步骤＋错误码＋准确版本状态＋重新校验）。UI 只显示 `user_message` 与错误码。
- 新增错误码：`CHANGE_NOT_REVIEWABLE`、`REVIEW_IDEMPOTENCY_CONFLICT`、`REVIEW_PERSISTENCE_FAILED`、`RELEASE_BLOCKED`、`RELEASE_MIRROR_REPAIR_REQUIRED`。

## RED 命令与失败证据

```bash
.venv/bin/python -m pytest \
  tests/integration/use_cases/test_review_change_request.py \
  tests/integration/use_cases/test_publish_baseline.py \
  tests/integration/recovery/test_reconciliation.py \
  tests/e2e/test_release_flow.py -q
```

首轮：`11 failed, 47 passed`。失败分别为：复核 UoW 并发场景把“已被他人复核”误报为幂等冲突（应为 `CHANGE_NOT_REVIEWABLE`）；e2e AppTest 抽取函数源码导致模块级类型注解与全局引用（`ChangeRequest`/`RepairResult`/`make_change`）在隔离上下文 NameError。修复 UoW 分支顺序与 e2e 函数内引用后：`6 failed`，剩余为 AppTest `session_state.get` 不支持与 radio options 断言形式问题。

## GREEN 命令与结果

同上 focused 命令：`58 passed in 1.77s`。

关联回归（release_policy、state_transition、manifest_store、repositories、record_decision、home/lint/query e2e）：`93 passed in 2.19s`。

## 完整验证结果（2026-08-04 整改轮，当前有效结果）

- 全套：`.venv/bin/python -m pytest -q` → `569 passed in 7.91s`（首轮 529＋整改新增 40）。
- 覆盖率：`.venv/bin/python -m pytest --cov=src/domain --cov=src/application --cov-report=term -q` → `569 passed`，domain＋application 总覆盖率 `95%`（TOTAL 2045 行、缺 103 行），门槛 90% 通过。
- 静态检查：`.venv/bin/ruff check src tests` → `All checks passed!`；`.venv/bin/ruff format --check src tests` → `128 files already formatted`。
- 编译：`.venv/bin/python -m compileall -q src scripts streamlit_app.py` → exit 0。
- 补丁空白：`git diff --check` → exit 0。

以下为首轮（2026-08-03）历史结果，仅作沿革记录，不作当前结论：

- 首轮全套 `529 passed`、覆盖率 `95.06%`、`123 files already formatted`。

## 真实组合根验证（隔离临时项目根目录）

在 `/tmp/t10_qa/project`（由共享夹具生成 pending_approval 变更＋真实 config）上执行：

- `build_container()` → 候选读取、复核、发布服务齐备，ReleaseGuard 未阻断；
- `ReviewChangeRequest(approve)` → `approved`；
- `PublishBaseline` → `LLD-724_2` 生效，父版本 `BASE-LLD-724_1`；
- 重建容器启动对账通过；首页 Dashboard 显示 `LLD-724_2` 且 `integrity_ok=True`。
- QA 数据全部位于 `/tmp/t10_qa`，未触碰仓库 `data/`（仓库无 `data/` 目录提交）。

## 提交哈希

- 实现提交：`3b20512` — `feat: add atomic governed baseline release`（分支 `codex/t10-atomic-release`，已合入 `feat/lightweight-t01`）

## 已知风险／未完成项

- 真实浏览器（1440×1024 与 390×844）验收两轮均受阻于环境：2026-08-03 与 2026-08-04 整改轮复查时 WebBridge 守护进程在线（v1.11.3），但浏览器扩展未连接（两次 `list_tabs` 均返回 `no extension connected`），无法驱动真实浏览器。发布成功/失败/重试流程、双列与窄屏布局、唯一主按钮、确认弹窗由 `tests/e2e/test_release_flow.py` 的 AppTest 在真实渲染管线覆盖。扩展恢复连接后需立即补做真实浏览器核验（无横向溢出、控制台无 error/warn、成功后首页立即显示新版本），见 v2 修改建议 T10-4 清单中唯一未勾选的原因。
- Manifest durability uncertain 且 Manifest 不可读时保守处理为不删除证据并报 `BASELINE_INTEGRITY_FAILED`，需人工介入核验；该路径按交接文档“禁止凭异常类型猜测”设计。

## Spec 对照自查

- [x] 四种人工复核可审计、幂等、受状态机约束；同 key 同命令幂等、同 key 不同命令 fail closed。
- [x] 未批准（pending/rejected/deferred/needs_info/published）绝对不能发布。
- [x] 发布成功后新 Manifest 生效、父版本可见、旧版 superseded 可历史查询。
- [x] Manifest 替换前任何失败保持旧版（锁、校验、文件、候选、提交、替换失败逐一测试）。
- [x] durability uncertain 重新读取 Manifest 判定权威状态；未替换目录隔离不静默删除。
- [x] SQLite 镜像失败可按 Manifest 自动修复；修复失败返回 `RELEASE_MIRROR_REPAIR_REQUIRED` 并禁用发布。
- [x] 发布失败保留 `approved`，重试只调 Publish，不重复 Review。
- [x] 启动对账幂等修复；Manifest 无效时不反向覆盖；不合成 Project；pending 变更不被伪造成 published。
- [x] 发布页满足设计结构、唯一主按钮、确认弹窗原文、成功/失败状态与“重新校验”。
- [x] 测试数据全部使用 tmp_path/隔离临时根目录，未提交运行产物。
- [x] 未进入 T11（无完整追溯页、市场证据缺口、成本计算、损益测算）。

## 整改轮（2026-08-04，按 v2 修改建议）

分支 `codex/t10-remediation`，基线 `3b20512`。整改内容：

- **T10-0**：修复 `publish_baseline.py` 的 Ruff F821（补 `ChangeRequest` 导入）；更新 `test_publish_baseline._use_case()` 注入 `sources=env.sources` 与真实 `SqliteIssueRepository`；`test_reconciliation._publish_manifest()` 传 `parent_version=` 并接收 `build_release_cards()` 完整卡集；`RunQuery` 全部构造点注入 `BaselineCardReader`（单元用严格内存 fake、集成用 `LocalBaselineCardReader`、golden 不退回 SQLite）；全部 `ReleaseUnitOfWork.publish()` fake 签名补齐 `new_cards`/`relations`；旧断言改为“新版本是完整快照”。65 个回归失败全部关闭。
- **T10-1**：`ReleaseUnitOfWork.publish()` 增加父版本 `full_document_sha256`/`card_snapshot_sha256` 参数，在同一事务内 supersede 前回填父行（按 id＋project 更新，rowcount≠1 报 `PARENT_BASELINE_NOT_FOUND`）；`_mirror_matches()` 纳入两哈希比较；`rebuild_current_from_manifest()` 回填；`bootstrap_demo.py` 仅对两哈希为 NULL 且三内容哈希验证通过的行补齐。`LocalBaselineCardReader` 路径收紧为精确 `data/obsidian_vault/02_Current_Baseline/{version}/cards.json`（version 非空且 `Path(version).name==version` 防穿越），共享纯函数 `parse_card_snapshot(raw_bytes, project_id, version)` 统一结构/重复 ID/版本归属校验；对账 `_verified_manifest_cards()` 单次读 bytes 同份驱动 SHA-256 与解析。
- **T10-2**：新增 `tests/integration/use_cases/test_publish_then_query.py` 4 项（发布→当前→历史→当前全流程版本一致；篡改历史/当前 `cards.json` 返回完整性错误且模型零调用；历史查询不继承未来子版本来源）。`_eligible_source_versions()` 硬化：重复版本 `BASELINE_DUPLICATE_VERSION`、断链 `BASELINE_PARENT_CHAIN_BROKEN`、循环 `BASELINE_PARENT_CHAIN_CYCLE`，均 fail closed 且同项目过滤。
- **T10-3**：`_validate_formal_sources()` 严格化——模块级 `_parse_source_ref`（`SOURCE_ID` 或 `SOURCE_ID:CITATION_ID`；空源/空 citation/多冒号 `CITATION_INVALID`/`PUBLISH_SOURCE_REF_INVALID:{card}`；空 refs `PUBLISH_CARD_SOURCE_REQUIRED`；所有引用逐一校验不跳过）；IssueEvidence 一一对应（0 匹配 `PUBLISH_EVIDENCE_NOT_IN_ISSUE`，>1 `PUBLISH_EVIDENCE_AMBIGUOUS:{cid}`），校验在创建临时目录之前。UoW `_validate_mirror_payload()` 在 SQL 前校验卡片 project/version 归属（`RELEASE_MIRROR_CARD_MISMATCH`）与关系两端 ⊆ {change_id, new_baseline.id, superseded_id}（`RELEASE_MIRROR_RELATION_MISMATCH`）；关系写入改为“同 ID 同事实跳过幂等、同 ID 不同事实 `RELEASE_MIRROR_RELATION_CONFLICT`”，不使用 INSERT OR IGNORE。

### T10-A01～A19 验收映射

| 验收 | 通过用例 |
|---|---|
| A01 只改一张发布、新快照全为目标版本 | `test_publish_success_replaces_manifest_and_mirrors_atomically`（断言 card_count、未改卡随快照升入目标版本） |
| A02 full.md 目标版本声明恰好一次 | 同上（声明计数断言）＋ `test_ambiguous_full_document_target_fails_closed` |
| A03 父快照混合版本拒绝 | `test_publish_success_replaces_manifest_and_mirrors_atomically` 前置父快照校验＋ `test_candidate_validation_failure_keeps_old_manifest` |
| A04 版本声明缺失或重复 | `test_ambiguous_full_document_target_fails_closed` ＋ manifest_store 候选校验用例 |
| A05 卡片 ID 重复/目标卡缺失 | `test_reader_rejects_duplicate_card_ids`、`test_reader_rejects_invalid_structure` ＋发布候选校验 |
| A06 发布后立即当前查询 | `test_publish_then_current_and_historical_queries_stay_version_consistent` |
| A07 历史 `_1` 查询 | 同上（历史段断言父版本旧正文） |
| A08 篡改历史 cards.json | `test_tampered_historical_snapshot_fails_closed_without_model_call` |
| A09 当前→历史→当前一致 | `test_publish_then_current_and_historical_queries_stay_version_consistent` 三段断言 |
| A10 SQLite 当前卡与快照一致 | `test_uow_mirrors_cards_relations_and_parent_hashes_atomically` |
| A11 镜像中途失败回滚并按 Manifest 重建 | `test_sqlite_mirror_failure_repaired_returns_success` |
| A12 污染后重启对账修复 | `test_mirror_mismatch_on_baseline_hash_drift_is_repaired` |
| A13 重建失败 ReleaseGuard 阻断 | `test_sqlite_mirror_failure_unrepaired_blocks_and_reports` |
| A14 重复对账幂等 | `test_rebuild_is_idempotent` |
| A15 沙箱证据阻断发布 | `test_publish_rejects_sandbox_evidence_source` |
| A16 引用来源不存在 | `test_publish_rejects_missing_card_source_and_keeps_old_release_tree` |
| A17 跨项目来源 | `test_publish_rejects_cross_project_evidence_source` |
| A18 权威级别不足 | `test_publish_rejects_non_formal_evidence_authority` |
| A19 全部合格正式来源正常发布 | `test_publish_success_replaces_manifest_and_mirrors_atomically` |

额外边界：未导入完成来源（`test_publish_rejects_unimported_card_source`）、重复 citation（`test_publish_rejects_ambiguous_issue_evidence_citation`）、非法 source_ref 形状参数化（`test_publish_rejects_invalid_card_source_refs`）、模型层空 refs 边界（`test_publish_rejects_model_invalid_card_source_refs_at_snapshot_boundary`）、UoW 归属与关系冲突 5 项（`test_release_uow_mirror.py`）、reader 9 项（`test_baseline_card_reader.py`）、父链边界 5 项（test_run_query.py 链组）、升级前父行哈希回填（`test_publish_backfills_asset_hashes_for_pre_upgrade_parent_row`、`test_bootstrap_backfills_pre_upgrade_baseline_hashes`）。

### 关键设计决策（v2 文档未写明、实现中裁定）

- **来源继承语义**：`_eligible_source_versions()` 采用祖先链规则——目标版本自身＋沿 `parent_baseline_id` 上溯的全部祖先版本来源均可继承。v2 文档只要求“父链循环/断链/重复/跨项目 fail closed、历史不继承未来子版本”，未规定继承粒度；选祖先链是因为历史版本的卡可能引用任一前代导入的来源，只继承直接父代会误伤合法历史查询。该规则已被 `test_query_inherits_sources_along_the_same_project_parent_chain` 与 `test_historical_query_never_inherits_future_child_sources` 双向钉死。
- **Relation 写入冲突策略**：发布镜像的关系表写入不用 INSERT OR IGNORE，改为先查同 ID：事实一致则幂等跳过（支撑重试），事实不同报 `RELEASE_MIRROR_RELATION_CONFLICT`（防静默吞冲突）。
- **发布失败保持 `approved` 语义**：A15–A18 各用例统一断言失败时 ChangeRequest 仍为 APPROVED、旧版本目录树与 Manifest/SQLite 不变（helper `_assert_release_tree_and_state_unchanged`）。

## v3 整改轮（2026-08-05，分支 codex/v3-remediation，提交 b3845d5）

依据 `docs/superpowers/handoffs/2026-08-04-t10-t11-acceptance-report-and-remediation-v3.md` 执行，范围 M1/M2/M4 中 T10 侧：

- **M1（T10 侧）**：bootstrap 基线具备真实业务语义（正式来源归档带背景章节、真实 chunk 引用、derived_from 关系、允许外部模型）；联合验收脚本改为验收侧独立预期（`EXPECTED_INITIAL_RULE`/`EXPECTED_PUBLISHED_RULE`）并带 V3-A02~A05 标签。
- **M2 发布闸前移**：新增错误码 `PUBLISH_SOURCE_INTEGRITY_FAILED` / `PUBLISH_CITATION_UNVERIFIABLE`；`publish_baseline` 注入 `SourceMaterialReader` 协议（container 接 `LocalQueryMaterialReader(project_root)`）；`_parse_source_ref` 返回 `(source_id, citation_id)`；生效卡须至少一条可定位 citation（裸 ID 仅补充）；变更单 evidence 逐一复验（基线侧证据走 read_baseline + locator/excerpt 分支）；全部验证在创建临时目录之前执行。`run_lint` 比较侧 citation_id 改用真实 `fragment.fragment_id`。
- **V3 新增测试**：A01（test_manifest_store.py）、A06~A12 + 基线侧证据正反 2 例（test_publish_baseline.py，44 passed）。
- **M4 真实浏览器验收（原"环境阻塞"已解除）**：官方 WebBridge 扩展 v1.11.5 经 External Extensions 自动安装连上守护进程；隔离工程 `/tmp/t10t11_browser` + 隔离 Chrome（/tmp/wb-profile）双视口实测：弹窗 358×392 在 390×844 视口内；篡改归档触发 `PUBLISH_SOURCE_INTEGRITY_FAILED` 且旧版本/已批准状态保持、还原后免重复批准重试成功（LLD-724_2 生效）。证据 `evidence/t10-t11/browser/`（5 图 + browser-acceptance.md，含输入方式如实说明）。
- **验证**：全量 659 passed（647+12 新），domain+application 覆盖率 95%，ruff check/format、compileall、git diff --check 全过。

## v4 整改轮（2026-08-05，分支 codex/v4-remediation，提交 2b02b56）

依据 `docs/superpowers/handoffs/2026-08-05-t10-t11-independent-review-remediation-v4.md`（独立复核）执行：

- **V4-P1-01 关闭**：正式来源证据在 citation 定位与 excerpt 匹配之外，新增 `document_version == material.document_version`、`page_or_section == fragment.locator` 全字段校验，任一不符统一 `PUBLISH_CITATION_UNVERIFIABLE` / `PUBLISH_EVIDENCE_CITATION_UNVERIFIABLE:<citation_id>`；不改写 evidence、不回退其他 fragment。
- **V4-P1-02 关闭（T10 侧）**：当前基线证据必须命中共享 citation 身份映射——`src/application/use_cases/baseline_citations.py` 为 run_lint 与 publish_baseline 共用的唯一生成规则（CIT-BASE-{index:03d} 兼容格式，条目含 citation ID/卡片 ID/基线版本/locator/excerpt）；发布侧按 Manifest 卡片快照重建映射并全字段比对，任意 citation ID fail closed。
- **V4-A01~A07 新增**：正式来源伪造版本/伪造 locator/四项全对；基线侧伪造版本/伪造 citation/伪造 locator/全对；失败用例均断言变更保持 approved、Manifest 与镜像不变、目标目录未生效、恢复合法证据后免重复批准直接重试成功。release_env 夹具 FULL_DOCUMENT 补上接口约束段（基线 full.md 覆盖全部卡正文的真实不变式），证据 page_or_section 改用真实 extractor locator。
- **验证**：专项 49 passed；全量 664 passed（659+5 净增）；覆盖率 95%；ruff/compileall/diff check 全过；联合 14 步全新环境 PASS。
- **浏览器换证**：以本提交重拍双视口 6 图（篡改失败→回退→重试全链路），证据与测量见 `evidence/t10-t11/browser/browser-acceptance.md`（验收 SHA 已更新为 2b02b56）。
