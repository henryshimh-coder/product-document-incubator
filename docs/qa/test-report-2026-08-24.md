# T13 测试报告（黄金测试 / E2E / 安全 / 设计验收）

- 报告任务：Task 13（黄金测试、E2E、安全测试和设计验收）
- 执行时间：2026-08-07 至 2026-08-09（最终全量复跑 2026-08-09 凌晨）
- 基线版本：分支 `feat/lightweight-t01`，基线 HEAD `fa5cc3970ba03a747b7073a183e8dce7668e6fa6`（T13 提交以此为基础；最终提交 SHA 见本报告末尾与进度台账）
- 运行方式：`.venv/bin/python -m pytest`（计划中的 `uv run` 一律以项目虚拟环境等价执行，已逐条核对无差异）
- 演示外部模型侧：本地 mock 网关（与联合验收同构的 fixtures，复用 `tests/e2e/harness.py` 工厂函数）；本轮未连接真实 Dify，属如实说明的替代。

## 一、全量自动测试

| 指标 | 数值 |
| --- | --- |
| 测试总数 | 725 |
| 通过 | 725 |
| 失败 | 0 |
| 领域 + application 覆盖率（计划门槛口径） | 95.40%（2587 语句，119 未覆盖；门槛 ≥85%，达标） |
| 全 src 覆盖率（参考口径） | 93%（5800 语句，426 未覆盖） |

执行命令：

```bash
.venv/bin/python -m pytest -q --cov=src/domain --cov=src/application \
  --cov-report=term --cov-fail-under=85
# Required test coverage of 85% reached. Total coverage: 95.40%. 725 passed.
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

- `tests/e2e` + `tests/security` 合计 76 项，全部通过。
- 计划点名的 4 个 E2E 场景各 1 项共 5 测试，全部通过：
  - 完整成功链路（导入→查询→自检→会议决定→变更单→审批→发布→Manifest/基线落账）：`test_full_success.py`
  - 实时超时回退精确缓存并继续：`test_realtime_timeout_fallback.py`
  - 发布失败保持旧版本（Manifest 逐字节不变）：`test_release_failure.py`
  - 安全阻断（L3 资料绝不发起模型调用）：`test_security_block.py`
- 安全测试要点：
  - Prompt 注入文本只落入 `inputs.source_chunks[...]`，顶层键全集不含越权字段；
  - 证件号/手机号在出站前掩码为 `[已脱敏:id_card]` / `[已脱敏:phone]`，日志与安全证明复核无残留（`tests/security/test_log_redaction.py`、`test_prompt_injection.py`）。

## 四、三次连续全流程（计划 Step 9）

```text
RUN 1: RESET_OK snapshot=initial → VALIDATION_OK baseline=LLD-724_1 → test_full_success 1 passed
RUN 2: RESET_OK snapshot=initial → VALIDATION_OK baseline=LLD-724_1 → test_full_success 1 passed
RUN 3: RESET_OK snapshot=initial → VALIDATION_OK baseline=LLD-724_1 → test_full_success 1 passed
```

三次均无阻断，每次开始前重置成功。重置恢复出的 `data/obsidian_vault/`、`data/source_archive/` 为可从已跟踪快照再生的运行态，已加入 `.gitignore`，复跑后工作区无残留。

## 五、UI 设计验收（1440×1024）

逐页验收 10 条全部通过，详见 [ui-acceptance-1440x1024.md](ui-acceptance-1440x1024.md) 与 `docs/qa/ui-1440x1024/` 下 8 张真实浏览器截图。验收使用真实 Chrome（WebBridge 扩展驱动），非静态渲染推断。

## 六、验收期间发现并修复的缺陷（4 项，均已回归）

1. 导入页适用版本硬编码 `LLD-724_1` → 改为从 dashboard 视图读取当前基线版本（`src/ui/pages/ingest.py`）。
2. 自检/查询覆盖率预算越界时错误消息笼统（误报为「资料尚未完成脱敏确认」）→ 补齐与导入同约定的预检，抛出准确 `OUTBOUND_COVERAGE_EXCEEDED`（`run_lint.py`、`run_query.py`）。
3. 旧 UI 测试 9 个用例因导入页默认值变化失败 → 显式设置适用版本（`tests/e2e/test_ingest_flow.py`）。
4. 追溯主链断链：ingest 来源冲突问题未持久化 `target_rule_id` 与规则卡回连关系，发布后追溯主链仍终止于已替代基线 → `import_source._to_domain` 持久化回连（与 run_lint 同约定）、`IngestUnitOfWork.complete` 补齐 `target_rule_id` 列、`build_trace._walk_chain` 同级多链决胜改为「链长 + 链尾节点发生时间」；新增回归测试 `test_trace_prefers_latest_complete_chain_on_ties`。

## 七、已知限制（如实说明）

1. 本轮 E2E/UI 验收的外部模型侧为本地 mock 网关，未连接真实 Dify；真实 Dify 联通性由 T14 交付封装前的 runbook 演练覆盖。
2. 发布 `LLD-724_3` 后 notices 被消费，全部当前资料范围下可比对材料只剩小基线，出站覆盖率 37.7% 超过 25% 预算，自检按设计 fail-closed 报 `OUTBOUND_COVERAGE_EXCEEDED`。这是治理正确行为；演示脚本应避免在发布后立即全量自检，或先导入新材料。
3. 首页 eyebrow「当前项目」上半部被 Streamlit 框架顶部工具栏裁切（框架层叠加），右上角「Deploy」为框架原生元素，均非产品 UI 缺陷。
4. 查询页「冻结缓存」标识未在浏览器会话中实演；缓存态语义由超时回退 E2E 与本地缓存条目佐证。
5. 覆盖率未覆盖项集中于 UI 层与防御性分支；计划门槛口径（domain+application）95.40%，全 src 口径 93%。

## 八、提交记录

| 提交 | 说明 |
| --- | --- |
| （见本文件 git 历史） | test: verify governed demo workflow and release safety |
| （见本文件 git 历史） | fix: 验收中发现的 4 项缺陷修复 |
| （见本文件 git 历史） | docs: T13 验收证据与测试报告 |

最终合入 SHA 与台账见 `.superpowers/sdd/2026-07-29-product-intelligence-lightweight/progress.md`。
