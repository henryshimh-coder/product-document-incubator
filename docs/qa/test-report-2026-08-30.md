# 测试报告：2026-08-30 实时演示冻结（v0.2.0-live-demo → v0.2.1-live-demo 整改）

> 执行日期：2026-08-10（T15，基线 `v0.1.1-lightweight`）。
> 口径说明：性能采样为**真实 Dify 调用**（干净克隆 + `.env` 三个互异 Key，模型硅基流动 `Pro/deepseek-ai/DeepSeek-V3.2`）；自动测试的外部模型侧为本地 mock 网关。两类事实分别记录，不互相替代。
> 修订说明：独立验收发现 Lint 运行时超时配置未生效（T15-R01）等四项问题，本报告按三段如实记录——原采样、独立复跑超时事件、整改后复验。脱敏样本与确定性重算见 [t15-performance-samples.json](evidence/t15-performance-samples.json) 与 `tests/unit/test_t15_performance_evidence.py`。

## 一、原采样（v0.2.0-live-demo，SHA `00df0c4`）：70/70 成功

七项操作各 10 次，于干净克隆执行，记录每次耗时与错误码：

| 操作 | n | min | P50 | P95 | max | 失败 | 目标 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 首页读取 | 10 | 0.001s | 0.002s | 0.003s | 0.003s | 0 | 3s | ✅ 达标 |
| Ingest 实时 | 10 | 15.605s | 16.861s | 19.861s | 19.861s | 0 | 45s | ✅ 达标 |
| Query 实时 | 10 | 11.360s | 12.858s | 14.234s | 14.234s | 0 | 20s | ✅ 达标 |
| Lint 实时 | 10 | 20.947s | 24.286s | 27.737s | 27.737s | 0 | 45s | ✅ 达标 |
| 缓存读取 | 10 | 0.001s | 0.001s | 0.013s | 0.013s | 0 | — | 记录值 |
| 发布 | 10 | 0.009s | 0.009s | 0.012s | 0.012s | 0 | — | 记录值 |
| 重置 | 10 | 0.308s | 0.311s | 0.316s | 0.316s | 0 | — | 记录值 |

- 失败率 0%，无超时。
- 采样方法说明：Ingest 每次重置后冷启动导入同一风险材料（重复导入会短路，故逐次重置）；Lint 一次准备对照材料后连续 10 次实时自检；Query 为连续 10 次实时查询；缓存读取为 frozen 快照下 Ingest 冻结缓存命中；发布/重置/首页为本地操作。
- ~~结论：`config/app.yaml` 超时配置无需改动~~。**该结论已被独立验收推翻，见第二节。**

## 二、独立复跑：Lint 实时超时（T15-R01 根因）

独立验收在干净克隆中以真实 Dify 复跑：Ingest、Query 成功，**Lint 返回 `MODEL_TIMEOUT: DIFY_TIMEOUT`**。

根因（如实记录）：`config/app.yaml` 顶层 `timeouts:` 声明 ingest 60s / query 30s / lint 60s，但修复前 `AppSettings` 无 timeout 字段、`_load_settings()` 只读取 `app` 节点忽略 `timeouts`，三个网关 `run()` 的隐式默认值 30 秒直接进入 HTTP 客户端。原采样 Lint P95 27.737s 紧贴 30 秒默认值上限，属于未暴露的计时侥幸，不是配置生效的证据。

连带问题：Lint 页面超时后没有完全匹配缓存接续入口，会直接中断现场演示（T15-R02）；性能原始记录仅留存于临时目录，未随冻结版本固化（T15-R03）；预检清单第 4 项只验证 Key 存在，未真实验证三个 Workflow（T15-R04）。

## 三、整改后复验（v0.2.1-live-demo）

### 3.1 代码与配置整改

- **T15-R01**：新增严格校验的 `WorkflowTimeouts` 配置模型（拒绝缺失节点/缺失字段/非整数/零/负值/超过 600 秒）；`load_settings()` 暴露三个超时值；组合根向三个网关**显式分别注入**对应超时，网关与 HTTP 客户端不再存在隐式默认。专项测试 10 项：配置读取/非法配置拒绝/组合根分路（`tests/unit/test_config.py`）、spy client 观测 60s 下 35–45s 模拟响应不被提前中断、query 仍在配置 30s 超时（`tests/integration/gateways/test_workflow_timeouts.py`）。
- **T15-R02**：Lint 页接入与查询页同构的超时缓存接续——实时超时后仅探测完全匹配（同材料、同版本）冻结缓存，命中则按缓存继续并展示「冻结缓存」、缓存生成时间与当前基线版本（`LintReport` 新增 `baseline_version` 溯源字段）；未命中展示「实时分析超时 · 未找到同材料、同版本的可用缓存」，不提供近似缓存。专项测试 4 项：页面接续/禁用态/溯源展示（`tests/e2e/test_lint_page.py`）+ 缓存接续后完成决定→审批→发布 E2E（`tests/e2e/test_lint_timeout_fallback.py`）。
- **T15-R03**：性能证据脱敏固化至 `docs/qa/evidence/t15-performance-samples.json`（每条仅操作/序号/开始时间/耗时/成功/公开错误码，轮次级代码 SHA 与环境标识）；`tests/unit/test_t15_performance_evidence.py` 提供确定性重算（P50 中位数、P95 最近秩、毫秒舍入），篡改样本或汇总即失败。
- **T15-R04**：预检清单第 4 项改为三个 Workflow 各自最小真实冒烟（成功/错误码/耗时/run ID，不记 Key）；演示脚本异常预案删除「Key 失效转本地治理演示」的错误说法，仅完全匹配缓存可继续。见 `docs/demo/`。

### 3.2 整改后 Lint 实时重采样

干净克隆 `/tmp/t15_r2`（SHA `5869748`，仅 `uv sync --frozen` + `.env` 三个互异 Key，零手工改动）真实 Dify 复验，2026-08-10 晚：

- **运行时观测**：`build_container()` 三个网关超时实测为 ingest 60s / query 30s / lint 60s，与 `config/app.yaml` 声明一致（修复前实测均为隐式 30s）。
- **三服务冒烟**：Ingest 实时 16.859s 成功，远端 `workflow_run_id` `dbee74a8-7326-4d7e-a1e2-fd61dbd39e3f`（`CALL-39B542237C1B4EF2AD0457BD61ABF70F` 为应用模型调用 ID，落库于 `model_call_logs`，不是 Dify 远端 ID）；Query 实时 14.116s 成功，远端 `workflow_run_id` `805056e9-c30e-48dc-91b3-3bae29f95d3b`（2026-08-10 22:00 经响应钩子补采）；错误码均无。
- **Lint 实时重采样 ×10**（同一对照材料，`all_current_sources` 范围）：n=10，min 21.184s，P50 24.915s，**P95 26.855s（目标 < 45s）**，max 26.855s，**失败 0**；十条的远端 `workflow_run_id` 逐次归档于 [t15-live-smoke-2026-08-10.json](evidence/t15-live-smoke-2026-08-10.json)（该文件同时归档 Ingest/Query 远端 ID 与 reviewer 独立 Lint ID `63ad32c9-033e-4929-a18d-610cf6ba5baf`；不含 Key、请求正文或未脱敏材料）。耗时样本见 [t15-performance-samples.json](evidence/t15-performance-samples.json) remediation 轮，确定性重算测试通过。
- 结论：R01 修复后 Lint 实时调用在 60 秒配置上限下稳定成功，原 30 秒隐性上限已消除；T15-R01/R02 关闭。

### 3.3 全量验证

```text
uv run pytest（整改后）                → 778 passed, 0 failed（770 + 8 整改新增）
ruff check/format（src scripts tests streamlit_app.py）→ All checks passed / 171 files already formatted
compileall src scripts tests           → 通过
git diff --check                       → 通过
uv lock --check                        → 65 packages 一致，未变更
连续三次 reset + 完整流程 E2E           → 3 × (RESET_OK → VALIDATION_OK → 5 passed)
干净克隆真实复验                        → /tmp/t15_r2 @ 5869748：运行时超时实测 60/30/60，
                                          三服务冒烟成功，Lint ×10 重采样 0 失败（见 3.2）
```

## 四、实时失败自动提示（T15 Step 2，原交付，保持有效）

- `src/ui/components/fallback_state.py`：`build_fallback_state()` 按计划规格实现——超时且无完全匹配缓存时 `cache_button_enabled=False`、文案「未找到同材料、同版本的可用缓存」；非超时错误码与空 task_type 直接拒绝。
- 查询页接线：实时超时后自动探测完全匹配（同问题、同版本、同材料）的冻结缓存——命中则以缓存继续并按「冻结缓存」口径标注；未命中展示「实时分析超时 · 未找到同材料、同版本的可用缓存」，不提供近似缓存。导入页既有超时反馈与缓存继续路径不变（T13 已实证）。本轮 R02 将同构接续扩展至自检页。
- 专项测试 6 项：`tests/unit/ui/test_fallback_state.py`（4 项）+ `tests/e2e/test_query_flow.py` 新增 2 项（超时→缓存继续、超时→无缓存禁用态）。

## 五、冻结声明（T15 Step 5，修订）

2026-08-30 之后：不自动刷新缓存、不升级依赖、不修改 Schema、不新增页面、不新增 Lint 类型；只修复演示阻断、数据错误和安全问题。演示脚本与预检清单见 `docs/demo/`。冻结版本为 **v0.2.1-live-demo**（v0.1.0/v0.1.1/v0.2.0 历史标签保留不移动）。

## 六、验收口径对照

| 计划验收 | 证据 |
| --- | --- |
| 现场可优先使用实时 Dify | 原采样 70/70 达标（第一节）；整改后 Lint 重采样 10/10、P95 26.855s（第三节）；T14-R03 真实全链证据 [dify-live-e2e-2026-08-09.json](dify-live-e2e-2026-08-09.json) |
| 实时异常时只使用完全匹配缓存 | 查询页与自检页超时→精确缓存探测接线 + UI 测试；T13/T15-R02 缓存接续 E2E（接续后完成决定/发布） |
| 切换后仍能真实执行人工决定和本地发布 | `tests/e2e/test_lint_timeout_fallback.py` 缓存接续→决定→审批→发布；发布操作采样 10/10 成功 |
