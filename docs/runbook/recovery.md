# 故障恢复手册

## 一键重置（首选恢复手段）

```bash
uv run python scripts/reset_demo.py --snapshot initial   # 或 frozen
uv run python scripts/validate_data.py                   # 必须输出 VALIDATION_OK
```

重置是事务化的：任一目标放置失败（含最终校验失败）都会整体回滚到重置前状态，并输出明确错误码。

## 常见故障与处置

| 现象 / 错误码 | 含义 | 处置 |
| --- | --- | --- |
| `RESET_LOCKED` | 应用持有状态共享锁，重置被互斥 | 停止应用进程后重试重置 |
| `APP_STATE_LOCKED` | 重置持有排他锁，应用拒绝启动 | 等重置完成后重新启动应用 |
| `VALIDATION_FAILED` | Manifest / SQLite / 缓存 / 归档不一致 | 以 `initial` 快照重置；仍失败则重置 `frozen` 后联系维护人 |
| `SNAPSHOT_SCHEMA_VERSION_UNSUPPORTED` / `SNAPSHOT_APP_VERSION_MISMATCH` | 快照与本应用版本不兼容 | 只使用本仓库 `data/demo_snapshots/` 内快照，不用旧版导出 |
| `ARCHIVE_CONFLICT` | 同路径来源归档哈希不一致 | 停止手工改动 `data/source_archive/`，用快照重置恢复 |
| `CACHE_NOT_FOUND` | 冻结缓存键（材料 + 版本等六要素）不精确匹配 | 改用实时查询，或确认查询材料与基线版本和冻结时一致 |
| `OUTBOUND_COVERAGE_EXCEEDED` | 出站载荷超 25% 覆盖率预算，fail-closed | 导入更多材料降低占比，或缩小输入片段；属治理设计行为 |
| `REDACTION_REQUIRED` | 材料未完成脱敏确认 | 按页面提示完成脱敏确认后重试 |
| 发布失败且提示 `PUBLISH_SOURCE_INTEGRITY_FAILED` | 来源归档被篡改，发布 fail-closed | 用快照重置恢复归档后重试发布（测试报告安全节有实证） |

## 状态与数据修复原则

- 运行态目录（`data/obsidian_vault/`、`data/source_archive/`、`data/local_state/`）不入库，任何损坏都以快照重置重建，不做手工修补。
- 已跟踪快照是只读事实来源；需要新快照时用 `scripts/export_snapshot.py` 导出并评审后入库。
- SQLite 为 Manifest 的镜像；两者不一致以 `validate_data.py` 报告为准排查，禁止直接手改数据库。

## 日志

- 事件日志与模型调用日志位于 `data/local_state/` 内；排查安全与出站问题时可配合 `tests/security` 的掩码与注入用例复核。
