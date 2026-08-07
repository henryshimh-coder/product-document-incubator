# Task 12 实施报告（演示数据、冻结缓存、快照和一键重置）

## 实现摘要

- **演示材料唯一生成规则**：`scripts/demo_materials.py` 提供四个 builder + `write_fixtures()` + CLI，生成 `tests/fixtures/sources/{current_product,risk_opinion,meeting_minutes,technical_review}.md`。`current_product.md` 逐字节等于 `scripts/bootstrap_demo.py` 的基线材料（直接引用常量）；`risk_opinion.md` 与 T10/T11 联合验收、浏览器验收所用风险材料逐字节同源（sha256=`739bf7df…`，对应浏览器验收来源 `SRC-739BF7DF1497A8C1`）；会议纪要/技术评审为新写演示材料（决议/评审范围 + 背景填充段落）。集成测试断言夹具与 builder 逐字节一致，杜绝夹具、bootstrap 与验收材料三方漂移。
- **快照体系**：`scripts/snapshot_common.py` 提供 `SnapshotManifest`（pydantic，字段与计划 Step 2 完全一致）、`capture_snapshot`、`verify_snapshot_payload`、`restore_snapshot`、`validate_data`、`freeze_demo_caches`；三个 CLI：`scripts/export_snapshot.py`、`scripts/reset_demo.py`、`scripts/validate_data.py`。
- **安全重置**：只覆盖四个显式目标（`data/local_state/product_intelligence.db`、`data/local_state/current_baseline.json`、`data/local_state/cache/`、`data/obsidian_vault/`），绝不删除 `data/source_archive/` 中的正式原始资料；恢复前核对载荷哈希（不符 fail closed `SNAPSHOT_PAYLOAD_MISMATCH`），恢复后自动运行 `validate_data`。
- **validate_data 五项检查**：Manifest 可解析 + 版本/基线 ID 核对 → 基线资产 sha → SQLite 镜像行比对（与 bootstrap 同口径）→ 缓存逐条（文件 sha、规范化 JSON、输出 schema、ingest/lint 键按身份重算）→ 来源归档（路径不越界、存在、sha+size 一致）。全部 fail closed，错误码逐类可区分。
- **冻结三类缓存**：风险意见 Ingest（key `65e3ae5c…`）、当前规则 Query（key `8eabfb63…`，question="当前目标客群是什么？"）、全范围 Lint（key `6555bafa…`）。载荷由真实抽取器从演示材料定位 chunk（chunk id/locator 非手造），写入前逐条过对应工作流输出 schema；每条记录 source SHA-256、baseline version、prompt version、model label、schema version（计划 Step 4 要求全覆盖）。
- **入库快照**：`data/demo_snapshots/{initial,frozen}/manifest.json + payload/`；initial 的 `database_sha256=ae7cfd92…` 跨构建环境字节一致（实测两个不同临时根导出哈希全等，`DETERMINISM_OK`）。

## 关键设计决策与边界

1. **WAL 落盘修复**：`src/infrastructure/db/connection.py` 使用 WAL 模式，直接复制主库文件会丢失近期提交（首轮 frozen 快照曾只剩一行缓存）。`capture_snapshot` 复制前执行 `PRAGMA wal_checkpoint(TRUNCATE)`；`restore_snapshot` 替换数据库时删除 `-wal`/`-shm` 侧车。
2. **跨构建字节确定性**：数据库 `source_records.archive_path` 存绝对路径，随构建根变化。载荷数据库统一规范化为 project_root 相对形式（`data/source_archive/...`，无法规范化 fail closed `ARCHIVE_PATH_UNNORMALIZABLE`），UPDATE 产生的页内碎片用 `VACUUM` 重建物理布局消除（同一逻辑内容曾因原始路径长度不同产生 14 字节物理差异）；恢复时 `_rewrite_archive_paths` 再改写为目标根绝对路径（无法改写 fail closed `ARCHIVE_PATH_UNREWRITABLE`）。frozen 快照含缓存写入时间戳（`cache_entries.created_at`），逐次构建哈希不同属预期，不规范化真实数据。
3. **缓存消费边界（如实说明）**：三类冻结缓存中只有 **Ingest 有真实运行时消费者**——`import_source` 在 `preferred_mode="cache"` 时不触碰网关，`task_id=INGEST-{sha16}` 为确定性值可被 `_validate_result` 校验，离线导入真实可用（本任务集成测试在断网 factory 下完成导入并产出冲突议题）。`run_query`/`run_lint` 没有 `preferred_mode` 入口，冻结的 Query/Lint 缓存是满足计划 Step 4 元数据要求的冻结工件，其完整性由 `validate_data` 逐项保证；本任务未夸大"无网络走完整流程"，运行时缓存消费面如需扩大属后续任务。
4. **缓存目录定位**：`AiCache.cache_dir` 默认相对 CWD 解析；`freeze_demo_caches` 显式纠正为 `project_root/data/local_state/cache`，离线测试以 `monkeypatch.chdir(demo_root)` 固定 CWD。
5. **载荷防篡改**：`restore_snapshot` 先 `verify_snapshot_payload`（四目标哈希逐一核对），再要求 project_root 已存在（`RESET_ROOT_MISSING`）；篡改任一目标分别命中 `SNAPSHOT_PAYLOAD_MISMATCH:database|manifest|vault|cache_index`（参数化测试覆盖三类）。

## T12 准入说明

v2 文档 §6 共 11 条门槛中 10 条客观项在 T10/T11 收口时已满足（T10/T11 已提交、668 测试全绿、覆盖率 95%、联合验收 14 步、浏览器证据齐全且最终 SHA 已补拍）；第 11 条（独立 reviewer 签认零 Critical/Important）由用户 2026-08-06 直接指令"继续完成T12"放行，特此如实注明。

## 计划 Step 1–6 验收映射

| 计划步骤 | 证据 |
|---|---|
| Step 1 重置测试（损坏→重置→validate ok + baseline=LLD-724_1） | `test_reset_restores_baseline_environment_after_corruption`（Manifest 垃圾 + Vault 全文删除 + 数据库删除后重置，report.ok 且 baseline=`LLD-724_1`） |
| Step 2 SnapshotManifest 字段 | `scripts/snapshot_common.py`（app_version 取自 pyproject.toml，缺失回退 0.1.0；created_at UTC） |
| Step 3 安全重置 + 不删正式原始资料 + 恢复后自动 validate | 四个显式目标常量 `SNAPSHOT_TARGETS`；`test_reset_never_touches_source_archive`（标记文件保留 + 基线归档 sha 不变）；`restore_snapshot` 末尾自动 `validate_data` |
| Step 4 冻结三类缓存 + 五类元数据 | `freeze_demo_caches`；`test_frozen_snapshot_cache_entries_carry_full_metadata`（逐行断言 source_sha256/baseline/prompt/model/schema、文件 sha、规范化 JSON、schema 校验、键重算） |
| Step 5 快照测试 + CLI | `tests/integration/scripts/` 22 项全过；CLI 冒烟 `RESET_OK snapshot=frozen` + `VALIDATION_OK baseline=LLD-724_1` |
| Step 6 独立提交 | 见下"提交"节 |

计划验收证据三条：

- **任意演示后可恢复初始状态**：损坏恢复测试 + CLI 冒烟 + `test_capture_is_byte_deterministic_across_build_roots`。
- **无网络时可用完全匹配缓存走完整流程**：`test_offline_ingest_from_frozen_cache`——恢复 frozen 后 http factory 任何请求直接 `AssertionError("NETWORK_FORBIDDEN")`，导入风险材料命中冻结缓存（`result_mode=CACHE`、`model_call_id=None`、`cache_generated_at` 非空）并产出 1 张冲突候选卡 + 1 个冲突议题。边界见"关键设计决策 3"。
- **正式原始资料不被重置删除**：`test_reset_never_touches_source_archive`。

## 完整验证结果（2026-08-06，当前有效）

- 全套：`.venv/bin/python -m pytest -q` → `690 passed`（T10/T11 收口 668 + T12 净增 22）。
- 覆盖率：domain+application `95%`（TOTAL 2479 行缺 116），门槛 90% 通过。
- 静态检查：`ruff check src tests scripts` 全过；`ruff format --check` 151 files；`compileall` 与 `git diff --check` 干净。
- 联合验收：全新临时根复跑 T10/T11 联合验收 14 步全 PASS，无回归。
- 确定性：两个不同临时根分别导出 initial 快照，manifest 七项字段（含 database_sha256）全等；载荷目录无 WAL 侧车。

## 提交

- 分支 `codex/t12-demo-snapshots`，feat 提交 `feat: add deterministic demo snapshots and reset`（scripts + tests + fixtures + data/demo_snapshots），docs 台账与报告单独提交；随后 fast-forward 合入 `feat/lightweight-t01`。T12 独立提交，未夹入任何 T10/T11 改动。
