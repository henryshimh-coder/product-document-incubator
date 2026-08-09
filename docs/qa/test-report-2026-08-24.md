# T13 测试报告（黄金测试 / E2E / 安全 / 设计验收）

- 报告任务：Task 13（黄金测试、E2E、安全测试和设计验收）
- 执行时间：2026-08-07 至 2026-08-09（含 2026-08-09 独立评审整改轮，最终全量复跑于整改后）
- 基线版本：分支 `feat/lightweight-t01`，基线 HEAD `fa5cc3970ba03a747b7073a183e8dce7668e6fa6`（T13 提交以此为基础；最终提交 SHA 见本报告末尾与进度台账）
- 运行方式：`.venv/bin/python -m pytest`（计划中的 `uv run` 一律以项目虚拟环境等价执行，已逐条核对无差异）
- 演示外部模型侧：本地 mock 网关（与联合验收同构的 fixtures，复用 `tests/e2e/harness.py` 工厂函数）；本轮未连接真实 Dify，属如实说明的替代。
- 静态检查范围（如实记录）：`ruff check src scripts tests streamlit_app.py` 与 `ruff format --check src scripts tests streamlit_app.py`（163 文件全部已格式化）。仓库级 `ruff check .` 会命中 `.superpowers` 内 T10/T11 留存证据脚本的既有格式问题，该脚本不属于 T13 生产代码门禁，本报告不将 scoped pass 表述为 repo-wide pass。

## 一、全量自动测试

| 指标 | 数值 |
| --- | --- |
| 测试总数 | 730 |
| 通过 | 730 |
| 失败 | 0 |
| 警告 | 0（T13-O04 已消除 Pydantic 枚举序列化警告） |
| 领域 + application 覆盖率（计划门槛口径） | 95.40%（2587 语句，119 未覆盖；门槛 ≥85%，达标） |
| 全 src 覆盖率（参考口径） | 93%（5800 语句，426 未覆盖） |

执行命令：

```bash
.venv/bin/python -m pytest -q --cov=src/domain --cov=src/application \
  --cov-report=term --cov-fail-under=85
# Required test coverage of 85% reached. Total coverage: 95.40%. 730 passed.
```

## 二、黄金指标（真实复算值）

| 指标 | 计划门槛 | 实测 |
| --- | --- | --- |
| Query 事实准确率 | ≥90% | 10/10 = 100% |
| Query 区间正确率 | — | 10/10 = 100% |
| Query 规则命中 | — | 10/10 = 100% |
| Query 关键引用覆盖 | 100% | 10/10 = 100% |
| Query 范围隔离 | 100% | 10/10 = 100% |
| Lint 召回 | ≥80% | 8 例 tp=7 fn=0 fp=0，recall = 1.0000 |
| Lint 严重度一致率 | — | 7/7 = 1.0000 |
| 重大问题双引用覆盖 | 100% | majors=2，双引用全部成立 |

`tests/golden`（test_query_golden.py、test_lint_golden.py）作为回归门禁纳入全量套件；上表为同口径逐例复算值。

## 三、E2E 与安全测试

- `tests/golden` + `tests/e2e` + `tests/security` 合计 83 项，全部通过，0 警告。
- 计划点名的 4 个 E2E 场景共 8 测试，全部通过：
  - 完整成功链路（**导入→查询→自检→决定→变更单→审批→发布→发布后查询**，计划规定顺序）：`test_full_success.py::test_complete_governed_product_change`
  - 发布产物断言的破坏性反证（产物被改回旧规则时断言必然失败）：`test_full_success.py::test_publish_artifact_assertions_fail_when_content_reverted`
  - SQLite 生效卡片断言的破坏性反证（`content`/`product_version`/`status` 任一被篡改时断言必然失败，参数化 3 例）：`test_full_success.py::test_sqlite_effective_card_assertions_fail_when_tampered`
  - 实时超时回退精确缓存并继续：`test_realtime_timeout_fallback.py`
  - 发布失败保持旧版本（Manifest 逐字节不变）：`test_release_failure.py`
  - 安全阻断（L3 资料绝不发起模型调用）：`test_security_block.py`
- 完整成功 E2E 的流程顺序证明（T13-R03 整改后）：出站网关调用的前三次必须为 `ingest → query → lint`（网关级顺序见证 `gateway_calls`）；变异实证——把 Query 移回导入前时断言以 `['query','ingest','lint'] != ['ingest','query','lint']` 失败，恢复规定顺序后通过。导入后、发布前的查询断言 `baseline_version == LLD-724_1` 且存在 citations。
- 完整成功 E2E 的三方一致证明（T13-R02/R03 整改后）：删除恒真断言，直接验证三方——发布产物（`full.md` 含新规则文本且不再含旧规则文本；`cards.json` 中 `RULE-LLD-001.content == 变更单 after_content`）；SQLite 生效卡片（`knowledge_cards` 直接断言 `id=RULE-LLD-001`、`product_version=LLD-724_2`、`status=effective`、`content=新规则文本`）；Manifest（指向新版本，旧基线 `superseded`、新基线 `effective`）。发布后实时查询的回答与版本分别等于新规则文本与 `LLD-724_2`。
- 安全测试要点：
  - Prompt 注入文本只落入 `inputs.source_chunks[...]`，顶层键全集不含越权字段；
  - 证件号/手机号在出站前掩码为 `[已脱敏:id_card]` / `[已脱敏:phone]`，日志与安全证明复核无残留（`tests/security/test_log_redaction.py`、`test_prompt_injection.py`）。

## 四、三次连续全流程（计划 Step 9，重置与测试分别 fail-fast）

```text
RUN 1: RESET_OK snapshot=initial → VALIDATION_OK baseline=LLD-724_1 → test_full_success 5 passed
RUN 2: RESET_OK snapshot=initial → VALIDATION_OK baseline=LLD-724_1 → test_full_success 5 passed
RUN 3: RESET_OK snapshot=initial → VALIDATION_OK baseline=LLD-724_1 → test_full_success 5 passed
```

执行形式（重置与测试各自 fail-fast，不以循环最终退出码代替恢复证据）：

```bash
for run_index in 1 2 3
do
  .venv/bin/python scripts/reset_demo.py --snapshot initial || exit 1
  .venv/bin/python -m pytest tests/e2e/test_full_success.py -q || exit 1
done
```

三次均无阻断，每次开始前重置成功。重置恢复出的 `data/obsidian_vault/`、`data/source_archive/` 为可从已跟踪快照再生的运行态，已加入 `.gitignore`，复跑后工作区无残留。

## 五、UI 设计验收（1440×1024）

逐页验收 10 条全部通过，详见 [ui-acceptance-1440x1024.md](ui-acceptance-1440x1024.md) 与 `docs/qa/ui-1440x1024/` 下 10 张真实浏览器截图（含 T13-R01 整改后补拍的查询页缓存态 03b/03c）。验收使用真实 Chrome（WebBridge 扩展驱动），非静态渲染推断。

## 六、验收期间发现并修复的缺陷（4 项，均已回归）

1. 导入页适用版本硬编码 `LLD-724_1` → 改为从 dashboard 视图读取当前基线版本（`src/ui/pages/ingest.py`）。
2. 自检/查询覆盖率预算越界时错误消息笼统（误报为「资料尚未完成脱敏确认」）→ 补齐与导入同约定的预检，抛出准确 `OUTBOUND_COVERAGE_EXCEEDED`（`run_lint.py`、`run_query.py`）。
3. 旧 UI 测试 9 个用例因导入页默认值变化失败 → 显式设置适用版本（`tests/e2e/test_ingest_flow.py`）。
4. 追溯主链断链：ingest 来源冲突问题未持久化 `target_rule_id` 与规则卡回连关系，发布后追溯主链仍终止于已替代基线 → `import_source._to_domain` 持久化回连（与 run_lint 同约定）、`IngestUnitOfWork.complete` 补齐 `target_rule_id` 列、`build_trace._walk_chain` 同级多链决胜改为「链长 + 链尾节点发生时间」；新增回归测试 `test_trace_prefers_latest_complete_chain_on_ties`。

## 六之二、独立评审整改（2026-08-09，T13-R01/T13-R02/T13-R03 已关闭）

- **T13-R01（查询页冻结缓存态证据缺口）**：查询页新增「查询方式：实时查询／冻结缓存」选择（`preferred_mode` 透传至 `RunQueryInput`）；缓存态标签由「演示缓存」更正为「冻结缓存」，与缓存生成时间同行单行展示。新增 UI 回归测试 `test_cached_result_shows_frozen_cache_baseline_and_generation_time`——移除缓存标签映射时失败、恢复后通过（变异已实证）。浏览器实演：从 frozen 快照恢复独立目录启动应用，选择冻结缓存执行「当前目标客群是什么？」，命中真实冻结缓存，1440×1024 截图同屏可见问题、回答、基线版本 `LLD-724_1` 与「冻结缓存 · 缓存生成时间 2026-08-07T04:20:46+00:00」，无横向滚动、无标签截断（`03b-query-cache.png`；查询方式区补充 `03c-query-cache-mode.png`）。
- **T13-R02（完整成功 E2E 恒真断言）**：见第三节。恒真断言已删除，发布产物内容、SQLite 生效卡片与 Manifest 三方版本/内容一致；破坏性反证用例常驻套件。
- **T13-R03（流程顺序与 SQLite 生效卡片验证缺口）**：完整成功 E2E 恢复计划规定顺序「导入→查询→自检」，导入后、发布前的查询断言 `baseline_version == LLD-724_1` 且存在 citations；新增网关级顺序见证 `gateway_calls`，断言出站调用前三次为 `ingest → query → lint`——变异实证：把查询移回导入前时断言失败，恢复后通过。SQLite 生效卡片由间接 baselines 推断改为直接 SQL 断言 `knowledge_cards` 的 `(id, product_version, status, content) == (RULE-LLD-001, LLD-724_2, effective, 新规则文本)`；新增参数化破坏性反证 `test_sqlite_effective_card_assertions_fail_when_tampered`（content/product_version/status 任一篡改时断言必然失败，3 例）。
- **T13-O04（Pydantic 枚举序列化警告）**：缓存 UI 测试改用 `CallResultMode.CACHE` 枚举替代 `"cache"` 字符串，警告消除；整改后专项 83 项与全量 730 项均为 0 警告。
- 评审非阻断项处理：静态检查范围已在报告头部如实写明（T13-O02）；三次连续演练已采用重置与测试分别 fail-fast 的形式（T13-O03，见第四节）；首页眉题裁切（T13-O01）保持 Minor 观察项，未在本轮改动。

## 七、已知限制（如实说明）

1. 本轮 E2E/UI 验收的外部模型侧为本地 mock 网关，未连接真实 Dify；真实 Dify 联通性由 T14 交付封装前的 runbook 演练覆盖。
2. 发布 `LLD-724_3` 后 notices 被消费，全部当前资料范围下可比对材料只剩小基线，出站覆盖率 37.7% 超过 25% 预算，自检按设计 fail-closed 报 `OUTBOUND_COVERAGE_EXCEEDED`。这是治理正确行为；演示脚本应避免在发布后立即全量自检，或先导入新材料。
3. 首页 eyebrow「当前项目」上半部被 Streamlit 框架顶部工具栏裁切（框架层叠加，T13-O01 Minor），右上角「Deploy」为框架原生元素，均非产品 UI 缺陷。
4. 覆盖率未覆盖项集中于 UI 层与防御性分支；计划门槛口径（domain+application）95.40%，全 src 口径 93%。

## 八、提交记录

| 提交 | 说明 |
| --- | --- |
| `c8d658a` | test: verify governed demo workflow and release safety |
| `9a7e6b9` | fix: 验收中发现的 4 项缺陷修复 |
| `8ff01c6` | docs: T13 验收证据与测试报告 |
| `7d73250` | fix: 独立评审整改 T13-R01（查询页缓存态 UI）与 T13-R02（发布产物断言） |
| `1fb35a2` | docs: 整改证据（03b/03c 截图）与报告、台账更新 |
| `001894e` | test: 独立评审整改 T13-R03（恢复规定流程顺序、SQLite 生效卡片直接断言）与 T13-O04（枚举警告消除） |
| （见本文件 git 历史） | docs: R03 整改报告与台账更新 |

最终合入 SHA 与台账见 `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/progress.md`。
