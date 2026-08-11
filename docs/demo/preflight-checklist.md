# 现场设备预检清单（演示前 30 分钟逐项打勾）

> 版本：`v0.2.0-live-demo`。任何一项不通过即按 `docs/demo/2026-09-live-demo-script.md` 的异常预案处理；连续两项不通过则改用备用演示视频。

| # | 检查项 | 通过标准 | 检查方法 |
| --- | --- | --- | --- |
| 1 | 电源和网络 | 电源接通；能访问 `https://api.dify.ai` 与模型供应商 | `curl -s -o /dev/null -w "%{http_code}" https://api.dify.ai/v1/models` 返回非 000 |
| 2 | 浏览器版本与 100% 缩放 | Chrome 近期版本；缩放 100% | 浏览器「设置 → 缩放」确认 |
| 3 | 1440×1024 分辨率 | 演示窗口 1440×1024，无横向滚动 | 参照 `docs/qa/ui-acceptance-1440x1024.md` 的验收截图口径 |
| 4 | Dify 三个 Key 可用 | 导入/查询/自检三服务实时就绪 | `.env` 三个互异 Key 在位；启动应用后三页均不显示「服务尚未就绪」；必要时先用「常用问题」做一次实时查询验证（约 13s） |
| 5 | 系统时间正确 | 与网络时间误差 < 1 分钟 | 对比 `time.is`；时间错误会导致缓存生成时间与追溯时间线不可信 |
| 6 | 初始快照校验通过 | `VALIDATION_OK baseline=LLD-724_1` | `uv run python scripts/reset_demo.py --snapshot initial && uv run python scripts/validate_data.py` |
| 7 | 缓存完整 | frozen 快照三类冻结缓存齐全且可命中 | `uv run python scripts/reset_demo.py --snapshot frozen && uv run python scripts/validate_data.py`，随后恢复 initial |
| 8 | 备用演示视频可播放 | 本地视频文件离线可播、音画正常 | 开场前完整试播一次 |
| 9 | 日志目录可写 | `data/local_state/` 可写，无磁盘不足告警 | 启动应用执行一次首页读取；`df -h` 确认余量 |
| 10 | 连续三次主流程无阻断 | 连续三轮 `reset → 全流程 E2E` 全过 | `for i in 1 2 3; do uv run python scripts/reset_demo.py && uv run pytest tests/e2e/test_full_success.py -q || exit 1; done` |

## 开场前最终状态

```text
uv sync --frozen          # 依赖锁定（65 packages）
uv run python scripts/reset_demo.py --snapshot initial   # 回到 LLD-724_1
uv run streamlit run streamlit_app.py --server.headless true
浏览器打开 http://localhost:8501，停在首页（00:00 环节）
```

## 冻结纪律（2026-08-30 之后）

不自动刷新缓存、不升级依赖、不修改 Schema、不新增页面、不新增 Lint 类型；只修复演示阻断、数据错误和安全问题。
