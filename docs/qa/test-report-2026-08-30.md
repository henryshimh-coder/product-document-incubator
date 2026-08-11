# 测试报告：2026-08-30 实时演示冻结（v0.2.0-live-demo）

> 执行日期：2026-08-10（T15，分支 `codex/t15`，基线 `v0.1.1-lightweight`）。  
> 口径说明：性能采样为**真实 Dify 调用**（干净克隆 + `.env` 三个互异 Key，模型硅基流动 `Pro/deepseek-ai/DeepSeek-V3.2`）；自动测试的外部模型侧为本地 mock 网关。两类事实分别记录，不互相替代。

## 一、固定性能采样（T15 Step 1）

七项操作各 10 次，于干净克隆（`/tmp/t15_perf`，SHA 见提交记录）执行，记录每次耗时与错误码；原始记录 `/tmp/t15_perf_{op}.json`，汇总如下：

| 操作 | n | min | P50 | P95 | max | 失败 | 目标 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 首页读取 | 10 | 0.001s | 0.002s | 0.003s | 0.003s | 0 | 3s | ✅ 达标 |
| Ingest 实时 | 10 | 15.605s | 16.861s | 19.861s | 19.861s | 0 | 45s | ✅ 达标 |
| Query 实时 | 10 | 11.360s | 12.858s | 14.234s | 14.234s | 0 | 20s | ✅ 达标 |
| Lint 实时 | 10 | 20.947s | 24.286s | 27.737s | 27.737s | 0 | 45s | ✅ 达标 |
| 缓存读取 | 10 | 0.001s | 0.001s | 0.013s | 0.013s | 0 | — | 记录值 |
| 发布 | 10 | 0.009s | 0.009s | 0.012s | 0.012s | 0 | — | 记录值 |
| 重置 | 10 | 0.308s | 0.311s | 0.316s | 0.316s | 0 | — | 记录值 |

- 失败率 0%，无超时；无需按预案缩小输入片段或固定演示数据。
- 采样方法说明：Ingest 每次重置后冷启动导入同一风险材料（重复导入会短路，故逐次重置）；Lint 一次准备对照材料后连续 10 次实时自检；Query 为连续 10 次实时查询；缓存读取为 frozen 快照下 Ingest 冻结缓存命中；发布/重置/首页为本地操作。
- 结论：`config/app.yaml`（超时 query 30s / ingest·lint 60s）、`dify_client.py`、`ai_cache.py` 不需要性能相关改动，保持冻结（如实记录：计划列出的三个 Modify 文件本轮零改动）。

## 二、实时失败自动提示（T15 Step 2）

- 新增 `src/ui/components/fallback_state.py`：`build_fallback_state()` 按计划规格实现——超时且无完全匹配缓存时 `cache_button_enabled=False`、文案「未找到同材料、同版本的可用缓存」；非超时错误码与空 task_type 直接拒绝。
- 查询页接线：实时超时后自动探测完全匹配（同问题、同版本、同材料）的冻结缓存——命中则以缓存继续并按「冻结缓存」口径标注；未命中展示「实时分析超时 · 未找到同材料、同版本的可用缓存」，不提供近似缓存。导入页既有超时反馈与缓存继续路径不变（T13 已实证）。
- 专项测试 6 项：`tests/unit/ui/test_fallback_state.py`（4 项，含计划同名用例与变异反证）+ `tests/e2e/test_query_flow.py` 新增 2 项（超时→缓存继续、超时→无缓存禁用态）。

## 三、全量验证（T15 Step 6）

```text
uv sync --frozen                        → 65 packages（锁文件未变）
uv run pytest                           → 758 passed, 0 failed（752 + 6 新增）
ruff check/format（src scripts tests streamlit_app.py）→ All checks passed / 168 files already formatted
compileall src scripts tests            → 通过
git diff --check                        → 通过
连续三次 reset + 完整流程 E2E            → 3 × (RESET_OK → VALIDATION_OK → 5 passed)（见提交记录）
```

## 四、冻结声明（T15 Step 5）

2026-08-30 之后：不自动刷新缓存、不升级依赖、不修改 Schema、不新增页面、不新增 Lint 类型；只修复演示阻断、数据错误和安全问题。演示脚本与预检清单见 `docs/demo/`。

## 五、验收口径对照

| 计划验收 | 证据 |
| --- | --- |
| 现场可优先使用实时 Dify | 性能采样全部为真实 Dify 调用且达标（第一节）；T14-R03 真实全链证据 [dify-live-e2e-2026-08-09.json](dify-live-e2e-2026-08-09.json) |
| 实时异常时只使用完全匹配缓存 | 查询页超时→精确缓存探测接线 + 2 项 UI 测试；T13 `test_realtime_timeout_fallback.py` 导入侧实证保持通过 |
| 切换后仍能真实执行人工决定和本地发布 | T13 超时 E2E（缓存继续后完成决定/发布）保持通过；发布操作采样 10/10 成功 |
