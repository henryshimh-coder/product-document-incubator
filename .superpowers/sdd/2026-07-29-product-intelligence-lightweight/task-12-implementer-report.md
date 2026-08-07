# Task 12 实施报告（演示数据、冻结缓存、快照和一键重置）

> 本报告为 T12 独立评审整改轮（2026-08-06）后的最终事实版。初版报告中"只有 Ingest 有运行时缓存消费者、Query/Lint 无 `preferred_mode` 入口"的边界声明已随 M3 整改作废：三类冻结缓存现在均有真实运行时消费者，断网完整主流程（含人工决定与本地发布）真实可用（T12-R13）。

## 实现摘要

- **演示材料唯一生成规则**：`scripts/demo_materials.py` 提供四个 builder + `write_fixtures()` + CLI，生成 `tests/fixtures/sources/{current_product,risk_opinion,meeting_minutes,technical_review}.md`。`current_product.md` 逐字节等于 `scripts/bootstrap_demo.py` 的基线材料；`risk_opinion.md` 与 T10/T11 联合验收、浏览器验收所用风险材料逐字节同源（sha256=`739bf7df…`，对应来源 `SRC-739BF7DF1497A8C1`）；会议纪要/技术评审为新写演示材料。集成测试断言夹具与 builder 逐字节一致。
- **快照体系（schema 1.1）**：`scripts/snapshot_common.py` 提供 `SnapshotManifest`（pydantic，`extra="forbid"`、frozen、Sha256 字段 pattern、UTC aware datetime、含 `source_archive_index_sha256` 第五类载荷哈希）、`capture_snapshot`、`verify_snapshot_payload`、`restore_snapshot`、`validate_data`、`freeze_demo_caches`；三个 CLI：`scripts/export_snapshot.py`、`scripts/reset_demo.py`、`scripts/validate_data.py`（CLI 捕获 `ValueError` 输出 `RESET_FAILED`/`EXPORT_FAILED errors=[...]` 并返回 1）。
- **输出路径保护（T12-P0-01 关闭）**：`capture_snapshot` 在任何写入前经 `_checked_output_dir` 拒绝输出目录与项目根相等/为根祖先/与四目标或 `source_archive` 双向重叠（`SNAPSHOT_OUTPUT_OVERLAP`）及其他不安全形态（`SNAPSHOT_OUTPUT_UNSAFE`），resolve 后判定。
- **原子捕获**：全部内容在输出旁的 staging 目录构建（路径规范化 + VACUUM + checkpoint → 来源种子复制并核对 sha → 写清单 → `verify_snapshot_payload` 复验），通过后 `_replace_tree` 受控换入（旧目录先改名、失败换回）；任一构建阶段失败仅清理 staging，旧快照逐字节保留（T12-R03）。
- **事务式恢复**：`restore_snapshot` 顺序为 `verify_snapshot_payload`（严格清单 + 五类载荷哈希）→ `_require_compatible` 兼容闸（`SNAPSHOT_SCHEMA_VERSION_UNSUPPORTED` / `SNAPSHOT_APP_VERSION_MISMATCH`）→ project 级重置锁（`RESET_LOCKED`）→ 来源种子规划（**只读 + `immutable=1`** 读载荷库：缺则补种、同路径同 sha 复用、同路径不同 sha 在覆盖任何目标前 fail closed `ARCHIVE_CONFLICT`）→ staging 复制四目标并改写归档路径 → 逐目标备份替换 → 任一失败 `_rollback` 完整回滚（删已放置、恢复已备份、删已补种，清理问题只记 note 不掩盖原异常）→ 最终 `validate_data` 失败同样回滚（T12-R08）。
- **干净目录独立恢复（T12-P1-01 关闭）**：initial/frozen 载荷自带数据库引用来源的种子归档（`payload/data/source_archive/...`），恢复到空临时根即可 `VALIDATION_OK baseline=LLD-724_1`，不依赖预先 bootstrap（T12-R04/R05）；恢复绝不删除目标根已有的额外正式来源（T12-R06）。
- **三类冻结缓存均有真实消费者（T12-P1-02 关闭）**：`RunQueryInput`/`RunLintInput` 新增 `preferred_mode`（默认 `realtime`）。`RunQuery`/`RunLint` 构造期注入 `AiCache` 与 `prompt_version`/`model_label` 标识：cache 模式按六字段身份（task_type、source_sha256、baseline_version、question、prompt_version、model_label、schema_version）`get_with_created_at` 精确查找，miss 即 `DomainError(CACHE_NOT_FOUND)`；命中后走与实时**完全相同**的输出验证（query 复用 `_validate_response`；lint 先经 `_validate_cached_issue_evidence` 把证据按 `side:citation_id` 锚定输入条目，unknown/字段不符在任何领域写入前 fail closed `LINT_CACHE_EVIDENCE_UNKNOWN_CITATION`/`LINT_CACHE_EVIDENCE_MISMATCH`），成功结果标记 `result_mode=CACHE`、`model_call_id=None`、`cache_generated_at` 回传。realtime 成功后 auto-put 缓存（写缓存失败不掩盖主结果）。
- **冻结缓存真实生成（scratch-harvest）**：`freeze_demo_caches` 复制整个 data+config 到 scratch（并先把 scratch 库内归档路径改写为 scratch 绝对路径），在 scratch 内以禁网 mock 网关真实运行 risk ingest/query/lint realtime（auto-put 落缓存），再以只读连接收割三行 `cache_entries` 回填正式根（校验身份键一致）。冻结载荷来自真实运行，非手造。
- **validate_data 五项检查**：Manifest 可解析 + 版本/基线 ID 核对 → 基线资产 sha → SQLite 镜像行比对 → 缓存逐条（文件 sha、规范化 JSON、输出 schema、键按身份重算）→ 来源归档（路径不越界、存在、sha+size 一致）。全部 fail closed。所有数据库连接显式关闭（`with connect(...)` 只管事务不关连接，已修复连接泄漏导致的 `-wal/-shm` 残留）。
- **入库快照**：`data/demo_snapshots/{initial,frozen}/manifest.json + payload/`（initial `database_sha256=ae7cfd92…`、frozen `database_sha256=34ac9323…`）；载荷数据库为 WAL 模式但捕获时已 VACUUM + checkpoint(TRUNCATE)，载荷读取一律 `mode=ro&immutable=1`，仓库载荷目录无 `-wal/-shm` 侧车（评审硬性门禁，回归测试锁定）。

## 关键设计决策与边界

1. **WAL 落盘与侧车治理**：`capture_snapshot` 复制前 checkpoint；staging 内路径改写后删除 `-wal/-shm`；载荷只读打开一律带 `immutable=1`（只读连接对可写目录的 WAL 库也会重建 `-shm/-wal`，这是评审残留的根因）；`validate_data`/`_validate_cache`/`_validate_source_archives` 连接显式关闭。
2. **跨构建字节确定性**：载荷库 `source_records.archive_path` 统一规范化为 project_root 相对形式 + VACUUM 消除页内碎片；恢复时 `_rewrite_archive_paths` 改写为目标根绝对路径（无法改写 fail closed）。initial 跨构建根导出哈希全等（`DETERMINISM_OK`）；frozen 含缓存 `created_at` 真实时间戳，逐次构建库哈希不同属预期。
3. **Query 缓存身份选择（如实记录）**：`effective` 范围 query 的 `source_sha256` 取**基线全文 sha256**（非逐卡拼接），因此发布新基线后旧查询缓存身份自动失效（T12-R11 末段断言）；`historical` 范围取快照 sha。lint 身份取比对包选中来源 sha（未选中时为参与材料 sha 集合的排序合成 sha）。
4. **缓存消费边界**：缓存命中只复用**模型输出载荷**，领域写入（议题/关系/决定/发布）全部在缓存命中后真实执行；缓存模式不产生 `model_call_logs`（T12-R10 断言为 0）。伪造缓存载荷走与实时相同的验证链并 fail closed（T12-R12）。
5. **兼容闸口径**：`schema_version` 不等即 `SNAPSHOT_SCHEMA_VERSION_UNSUPPORTED`（旧 1.0 快照被新闸拒绝属预期）；`app_version` 不等即 `SNAPSHOT_APP_VERSION_MISMATCH`；清单额外字段被 `extra="forbid"` 拒绝为 `SNAPSHOT_MANIFEST_INVALID`（T12-R09）。
6. **载荷防篡改**：五类载荷哈希逐一核对，篡改分别命中 `SNAPSHOT_PAYLOAD_MISMATCH:database|manifest|vault|cache_index|source_archive_index`（参数化测试覆盖）。

## T12 负向用例映射（R01–R15）

| 用例 | 实现/测试 |
|---|---|
| R01 输出==项目根 | `test_capture_refuses_output_equal_to_project_root`：写入前 `SNAPSHOT_OUTPUT_OVERLAP`，根指纹逐字节不变 |
| R02 输出为根祖先/四目标内部/source_archive 内部 | `test_capture_refuses_output_overlapping_protected_paths`：5 个危险输出全部拒绝 |
| R03 构建三注入点失败（copy/normalize/verify） | `test_capture_failure_preserves_previous_snapshot`：旧快照逐字节保留、无 staging 残留 |
| R04 initial 恢复空目录 | `test_restore_initial_into_empty_clean_root`：VALIDATION_OK + 种子归档=夹具字节 + 库内路径已改写 |
| R05 frozen 恢复空目录 | `test_restore_frozen_into_empty_clean_root`：三缓存 + 种子齐全 |
| R06 额外来源与标记文件保留 | `test_reset_preserves_extra_source_files` |
| R07 同路径来源哈希冲突 | `test_restore_refuses_conflicting_source_archive`：`ARCHIVE_CONFLICT` 先于任何覆盖，四目标指纹不变 |
| R08 各阶段失败回滚 | `test_restore_rolls_back_when_path_rewrite_fails` / `..._when_target_replace_fails` / `..._when_final_validation_fails` 三注入，回滚后真 validate ok、无 `.reset-*` 残留 |
| R09 版本伪造与额外字段 | `test_restore_refuses_forged_or_unknown_versions`：app 999→MISMATCH、schema 999→UNSUPPORTED、额外字段→MANIFEST_INVALID |
| R10 Query/Lint 完全匹配缓存断网可用 | `test_query_and_lint_serve_from_exact_frozen_cache_offline`：CACHE 标记齐全、`model_call_logs==0` |
| R11 六类身份字段不串用 | `test_cache_identity_fields_are_strict`：question 变化、prompt/model 服务级伪造、schema 身份层键分离（Literal 契约先行拒绝伪造值）、材料 +1 字节 ingest miss、发布后 query miss |
| R12 缓存载荷伪造 fail closed | `test_forged_query_cache_payload_fails_closed`（假 citation→`UNKNOWN_CITATION`、假版本→`BASELINE_VERSION_MISMATCH`，与实时同异常类型）+ `test_forged_lint_cache_payload_fails_closed`（假 citation/locator/版本→lint 专属错误码）；议题/关系/决定计数均不变 |
| R13 断网完整主流程 | `test_offline_cache_full_story_flow_reaches_publish`：恢复 frozen → 禁网 factory → cache ingest → cache query → cache lint → 人工决定 → 批准 → 发布 `LLD-724_2`，Manifest 落盘 |
| R14 连续三次重置 | `test_three_consecutive_resets_after_full_demo`：完整演示到 `LLD-724_2` 后连续 3 次 reset initial，每次 ok、四目标指纹一致、无 `-wal/-shm/.tmp/.reset-*/staging` 残留 |
| R15 浏览器失败证据 | `evidence/t10-t11/browser/release-failure-mobile-390x844.png`（2026-08-06 于 `46de3fd` 重拍）：失败告警、错误码 `PUBLISH_SOURCE_INTEGRITY_FAILED`、安全重试提示同屏可见；复验记录见 `browser-acceptance.md` |

另有锁占用 `RESET_LOCKED`、离线 ingest、缓存元数据、载荷篡改参数化、载荷无侧车扫描等回归测试在 `tests/integration/scripts/test_reset_demo.py`。

## T13 准入 17 条自检（评审 §6 逐条）

1. P0-01 关闭，重叠输出写入前拒绝 → R01/R02 ✓
2. initial/frozen 干净根独立恢复（自带种子，不依赖 bootstrap） → R04/R05 ✓
3. 保留现有正式来源、冲突先 fail closed → R06/R07 ✓
4. capture/restore staging + 受控替换 + 失败回滚 → R03/R08 ✓
5. 兼容字段 + 严格字段 + 全部载荷哈希参与恢复闸 → R09 + 篡改参数化 ✓
6. 三类缓存均有真实消费者 → `run_query.py`/`run_lint.py`/`import_source` + R10 ✓
7. 六类身份字段精确匹配不串用 → R11 ✓
8. 断网缓存主流程含人工决定与本地发布 → R13 ✓
9. R01–R15 全过，无 skip/xfail → `tests/integration/scripts` 41 项全过（全量输出无 skip） ✓
10. 专项 + 关联测试 + 全量全过 → `709 passed` ✓
11. 覆盖率 ≥90% → domain+application `95%`（TOTAL 2552 行缺 123） ✓
12. Ruff/format/compileall/`git diff --check` → 全过 ✓
13. initial/frozen 重新生成并入库，manifest 哈希与报告一致 → `46de3fd`（initial `ae7cfd92…`、frozen `34ac9323…`，`verify_snapshot_payload` 通过） ✓
14. 浏览器失败证据补齐，继承 Process Important 关闭 → R15 ✓
15. 报告与 progress 按最终事实更新，不再以离线 ingest 代替完整流程 → 本报告决策 3/摘要已改写 ✓
16. 整改提交边界清晰、工作区干净、无 T13 产物 → 分支 `codex/t12-remediation`，feat/docs 两提交，未建 `tests/e2e/harness.py` 等违禁产物 ✓
17. 全量测试与三轮重置后 Git 内载荷无 `-wal/-shm`/staging/backup 残留 → `test_repo_snapshot_payloads_have_no_residue` + 工作区实测 ✓

第 18 条（独立 reviewer 复验签认）留待评审方执行，本报告不代签。

## 独立评审第二轮整改（2026-08-06，提交 `7fefc1f`）

第一轮合入后评审再报 1 个 Critical + 2 个 Important，本轮全部关闭：

1. **Critical：任意普通目录仍可被快照覆盖** → `_checked_output_dir` 新增非快照拒止：输出目录已存在且含内容时，必须是快照形态（`manifest.json` + `payload/`）才允许被原子换入，否则 `SNAPSHOT_OUTPUT_NONSNAPSHOT` fail closed；`project_root/src` 等目录内的哨兵文件逐字节保留。快照形态目录允许重复捕获换入、空目录允许使用（两项回归同测锁定）。
2. **Important：重置锁未与应用协同** → 新增 `src/infrastructure/db/state_lock.py`（`STATE_LOCK_REL` 单一来源）：`build_container` 返回可用容器前持 `LOCK_SH|LOCK_NB`（重置进行中则 `APP_STATE_LOCKED` fail closed），`restore_snapshot` 持 `LOCK_EX|LOCK_NB`（应用运行则 `RESET_LOCKED`），分裂状态在锁层被排除；`AppContainer.close()` 幂等释放（进程退出时 flock 亦自动释放）。应用持锁期间重置被拒且四目标指纹不变、关闭后恢复成功，集成测试锁定。
3. **Important：来源种子部分复制失败无法完整回滚** → 种子复制改为「先登记回滚 + 临时文件 + `os.replace` 原子换入 + finally 清临时文件」：注入部分写入后失败，目标路径不出现残缺文件、无 `.seed-*.tmp` 残留，下一次恢复不命中 `ARCHIVE_CONFLICT`（负向测试锁定）。

对应测试：`test_capture_refuses_to_overwrite_non_snapshot_directory`、`test_capture_allows_recapture_over_snapshot_shaped_directory`、`test_restore_blocked_while_app_holds_state_lock`、`test_restore_rolls_back_partial_seed_copy`；R14 三连重置测试适配为「先 `container.close()` 再重置」。

## 独立评审第三轮整改（2026-08-06 深夜，提交 `f84858f`）

第二轮合入后评审再报 1 个 Critical + 1 个 Important，并给出最终修改标准，本轮全部关闭：

1. **Critical：只校验"快照外形"，仍可删除普通目录** → 按最终标准收紧 `_checked_output_dir`：已存在且非空的目录，顶层条目集合必须**恰好**为 `{manifest.json, payload/}`（任何约定外条目直接 `SNAPSHOT_OUTPUT_NONSNAPSHOT`），且必须通过**完整** `verify_snapshot_payload()`（严格清单解析 + 五类载荷哈希全对）才允许被替换；伪快照目录（无效清单 + payload + 业务哨兵文件）拒绝覆盖且哨兵逐字节保留，指定负向测试 `test_capture_refuses_invalid_snapshot_shaped_directory` 同时覆盖"有哨兵"与"仅约定两项但清单无效"两种形态。
2. **Important：应用获取共享锁的时间太晚** → `build_container` 重构为两段：确认 Manifest 存在后立即 `acquire_shared`（**早于** `migrate()`、Manifest 镜像对账与一切状态访问），主体装配移入 `_build_stateful_container`；`try/except BaseException` 保证构建失败必释放锁，成功时锁随容器持有。指定负向测试 `test_build_container_fails_closed_before_migrate_when_locked`：排他锁占用下 `migrate` 间谍零调用、立即 `APP_STATE_LOCKED`，锁释放后正常构建并持锁。

## 完整验证结果（2026-08-06 第三轮整改后，当前有效）

- 全套：`.venv/bin/python -m pytest -q` → `715 passed`（第二轮 713 + 第三轮净增 2）。
- 覆盖率：domain+application `95%`（TOTAL 2567 行缺 124），门槛 90% 通过。
- 静态检查：`ruff check src tests scripts` 全过；`ruff format --check` 153 files；`compileall` 与 `git diff --check` 干净。
- 联合验收：全新临时根复跑 T10/T11 联合验收 14 步全 PASS，无回归。
- CLI 冒烟：空目录 `--snapshot initial` / `--snapshot frozen` 恢复均 `RESET_OK` + `VALIDATION_OK baseline=LLD-724_1`，恢复目录零侧车/零 staging 残留。
- 浏览器复验（第一轮 R15）：`46de3fd` 上完整复现「篡改归档 → 发布失败（`PUBLISH_SOURCE_INTEGRITY_FAILED`）→ 还原重试成功」，服务端状态直查两端均正确，详见 `browser-acceptance.md`；第二、三轮改动均不涉及发布页行为。

## 提交

- 初版：分支 `codex/t12-demo-snapshots`，feat `feat: add deterministic demo snapshots and reset` + docs 提交，fast-forward 合入 `feat/lightweight-t01`。
- 整改第一轮：feat `46de3fd fix: close snapshot safety gaps and wire query lint cache consumers` + docs 提交。
- 整改第二轮：feat `7fefc1f fix: protect ordinary directories and coordinate reset lock with app` + docs 提交。
- 整改第三轮：feat `f84858f fix: require full payload verify before replace and lock before migrate`（scripts/src/tests）+ docs 提交；随后 fast-forward 合入 `feat/lightweight-t01`。T12 整改独立提交，未夹入任何 T13 产物。
