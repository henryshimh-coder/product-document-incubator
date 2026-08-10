# 2026-08-24 轻量交付清单（v0.1.0-lightweight）

> 交付窗口：2026-08-22—2026-08-24；封装执行：2026-08-09（分支 `codex/t14`，基线 HEAD `e79700e`，最终交付 SHA 以标签 `v0.1.0-lightweight` 为准）。
> 逐项状态均为已核实的仓库事实；验证命令与结果见各条目。

| # | 交付项 | 位置 / 证据 | 状态 |
| --- | --- | --- | --- |
| 1 | 源代码版本 | git 标签 `v0.1.0-lightweight`（随本清单一同入库的交付提交）；分支 `feat/lightweight-t01` | ✅ |
| 2 | 依赖锁文件 | `uv.lock`；`uv lock --check` 通过（64 packages 与 `pyproject.toml` 一致）；干净目录 `uv sync --frozen` 实测成功 | ✅ |
| 3 | 配置模板 | `.env.example`（容器自动加载，进程环境变量优先）、`config/{app,schema,lint_rules}.yaml`（含 `lint_input_contract_version: "2.0"`） | ✅ |
| 4 | 三个 Dify Workflow 导入说明 | [../runbook/dify-import.md](../runbook/dify-import.md)：创建/导入、三个互异 Key、输入输出契约、联通性演练步骤 | ✅ |
| 5 | SQLite 初始化 | `scripts/bootstrap_demo.py`（输出 `BOOTSTRAP_OK baseline=LLD-724_1`）；干净目录实测通过 | ✅ |
| 6 | 知识 Vault | `data/obsidian_vault/`（运行态，由 bootstrap/reset 再生，`.gitignore`）；快照内置基线与知识卡 | ✅ |
| 7 | 黄金测试数据 | `tests/golden/`（query 10 问 / lint 8 例）；纳入全量回归，指标见测试报告第二节 | ✅ |
| 8 | 冻结缓存 | `data/demo_snapshots/frozen/`（导入/查询/自检冻结缓存，元数据齐全）；缓存命中浏览器实证 `docs/qa/ui-1440x1024/03b-query-cache.png` | ✅ |
| 9 | 测试报告 | [../qa/test-report-2026-08-24.md](../qa/test-report-2026-08-24.md)：全量 730 项 0 失败 0 警告、覆盖率 95.40%、黄金指标全达标、83 项专项、三次重置演练；UI 验收 [../qa/ui-acceptance-1440x1024.md](../qa/ui-acceptance-1440x1024.md)（10 条全过，真实浏览器截图） | ✅ |
| 10 | 演示重置 | `scripts/reset_demo.py --snapshot initial|frozen`（事务化、与应用互斥锁协同）；连续三次 `RESET_OK → VALIDATION_OK → E2E 5 passed` 实证 | ✅ |
| 11 | 操作手册 | [README.md](../../README.md) + [../runbook/local-development.md](../runbook/local-development.md)、[demo-operation.md](../runbook/demo-operation.md)、[recovery.md](../runbook/recovery.md)、[dify-import.md](../runbook/dify-import.md) | ✅ |
| 12 | 已知限制 | README「已知限制」节：T13 外部模型侧为 mock 网关（真实 Dify 联通性按 dify-import 手册演练）、发布后自检覆盖率 fail-closed 为设计行为、Streamlit 框架层眉题裁切/Deploy 按钮 | ✅ |
| 13 | 材料安全复核 | 复核人：`shiminghao`（项目负责人，本机交付账号，可追责标识）；复核时间：2026-08-09（本地时区 UTC+8）；复核材料：`data/demo_snapshots/{initial,frozen}/payload/data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md`（两快照同源，SHA-256 `71d19c2f8be7cd21d3007a8fa5719728b39f0f2bdc96e21e7e77d124ad1638e9`，19731 字节）及快照内置基线/知识卡文本；复核结论：全部为脱敏模拟文本，无真实客户/证件/手机号；自动化佐证——`tests/security` 出站掩码与 prompt 注入隔离用例全量通过、发布完整性篡改 fail-closed 有浏览器实证；例外项：无 | ✅ |

## 交付前最终验证记录（计划 Step 4）

在交付提交前于仓库根目录执行，结果如实记录：

```text
uv run python scripts/reset_demo.py     → RESET_OK（见本节下方实测记录）
uv run python scripts/validate_data.py  → VALIDATION_OK baseline=LLD-724_1
uv run pytest                           → 730 passed, 0 failed
```

## 证据口径说明（启动证据 ≠ 业务流程证据）

- **启动证据（已实测）**：干净目录 `git clone` 后仅按 README 命令执行——`uv sync --frozen` → `cp .env.example .env` → `bootstrap_demo.py`（BOOTSTRAP_OK）→ `validate_data.py`（VALIDATION_OK）→ `streamlit run --server.headless true`（HTTP 200），全程无手工代码改动，2026-08-09 实测通过。该证据只证明干净设备可安装、初始化、校验并启动。
- **`.env` 装配证据（已实测）**：干净克隆中填写三个互异 Key（不手工 export、清除进程环境变量）后，默认 `build_container()` 的 import/query/lint 三项服务均装配；空 Key 时保持本地模式（`tests/unit/test_container_dotenv.py` 四项用例 + 干净克隆复跑）。
- **业务流程证据（真实 Dify）**：完整「导入→查询→自检→决定→审批→发布→追溯」需三个真实 Workflow Key，按 [dify-import.md](../runbook/dify-import.md) 第七节执行并单独记录；本地 mock 网关的完整流程 E2E（`tests/e2e/test_full_success.py`，5 项）已在干净克隆复跑通过，但不替代真实联通证据。
