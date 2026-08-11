# 现场设备预检清单（演示前 30 分钟逐项打勾）

> 版本：`v0.2.1-live-demo`。任何一项不通过即按 `docs/demo/2026-09-live-demo-script.md` 的异常预案处理；连续两项不通过则改用备用演示视频。

| # | 检查项 | 通过标准 | 检查方法 |
| --- | --- | --- | --- |
| 1 | 电源和网络 | 电源接通；能访问 `https://api.dify.ai` 与模型供应商 | `curl -s -o /dev/null -w "%{http_code}" https://api.dify.ai/v1/models` 返回非 000 |
| 2 | 浏览器版本与 100% 缩放 | Chrome 近期版本；缩放 100% | 浏览器「设置 → 缩放」确认 |
| 3 | 1440×1024 分辨率 | 演示窗口 1440×1024，无横向滚动 | 参照 `docs/qa/ui-acceptance-1440x1024.md` 的验收截图口径 |
| 4 | Dify 三个 Workflow 各自真实冒烟 | Ingest/Query/Lint 三服务各自一次真实调用成功 | 见下方「第 4 项：三 Workflow 冒烟规程」，逐项记录成功/错误码/耗时/run ID（严禁记录 Key）；任一失败须按规程验证对应完全匹配缓存可从 UI 命中，否则按异常预案处理 |
| 5 | 系统时间正确 | 与网络时间误差 < 1 分钟 | 对比 `time.is`；时间错误会导致缓存生成时间与追溯时间线不可信 |
| 6 | 初始快照校验通过 | `VALIDATION_OK baseline=LLD-724_1` | `uv run python scripts/reset_demo.py --snapshot initial && uv run python scripts/validate_data.py` |
| 7 | 缓存完整 | frozen 快照三类冻结缓存齐全且可命中 | `uv run python scripts/reset_demo.py --snapshot frozen && uv run python scripts/validate_data.py`，随后恢复 initial |
| 8 | 备用演示视频可播放 | 本地视频文件离线可播、音画正常 | 开场前完整试播一次（仓库不含视频工件，由现场负责人在演示设备上补证） |
| 9 | 日志目录可写 | `data/local_state/` 可写，无磁盘不足告警 | 启动应用执行一次首页读取；`df -h` 确认余量 |
| 10 | 连续三次主流程无阻断 | 连续三轮 `reset → 全流程 E2E` 全过 | `for i in 1 2 3; do uv run python scripts/reset_demo.py && uv run pytest tests/e2e/test_full_success.py -q || exit 1; done` |

## 第 4 项：三 Workflow 冒烟规程（T15-R04 修订）

只验证 Key 存在不等于 Workflow 可用。开场前依次执行三次**真实**最小调用（固定演示输入，模型为硅基流动 `Pro/deepseek-ai/DeepSeek-V3.2`），每次记录结果到下表（run ID 指追溯页可见的模型调用标识，严禁记录 Key 本身）：

```text
uv run python scripts/reset_demo.py --snapshot initial
uv run streamlit run streamlit_app.py --server.headless true
```

1. **Ingest 冒烟**：导入页以「实时分析」上传 `tests/fixtures/sources/risk_opinion.md`，期望成功（参考耗时约 17s，上限 60s）。
2. **Query 冒烟**：查询页以「实时查询」提问「当前目标客群是什么？」，期望成功（参考耗时约 13s，上限 30s）。
3. **Lint 冒烟**：自检页选「当前基线＋本次新资料」、填入第 1 步来源 ID，以「实时自检」运行，期望成功（参考耗时约 25s，上限 60s）。

| Workflow | 结果（成功/错误码） | 耗时（s） | run ID |
| --- | --- | --- | --- |
| Ingest |  |  |  |
| Query |  |  |  |
| Lint |  |  |  |

任一失败时：执行 `uv run python scripts/reset_demo.py --snapshot frozen`，在 UI 以「冻结缓存」方式对同一材料/问题重试，确认对应完全匹配缓存可命中并正常渲染（含「冻结缓存」标识与缓存生成时间）；缓存也不可得则该环节按异常预案改用备用演示视频。完成后恢复 initial 快照。

## 开场前最终状态

```text
uv sync --frozen          # 依赖锁定（65 packages）
uv run python scripts/reset_demo.py --snapshot initial   # 回到 LLD-724_1
uv run streamlit run streamlit_app.py --server.headless true
浏览器打开 http://localhost:8501，停在首页（00:00 环节）
```

## 冻结纪律（2026-08-30 之后）

不自动刷新缓存、不升级依赖、不修改 Schema、不新增页面、不新增 Lint 类型；只修复演示阻断、数据错误和安全问题。
