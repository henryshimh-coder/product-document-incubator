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
- **业务流程证据（真实 Dify，已实测）**：完整「导入→查询→自检→决定→审批→发布→追溯」已于 2026-08-09 在干净克隆中对真实 Dify 云工作流跑通，证据见下方「v0.1.1-lightweight 增补」节与 [dify-import.md](../runbook/dify-import.md) 第七节实录；本地 mock 网关的完整流程 E2E（`tests/e2e/test_full_success.py`，5 项）已在干净克隆复跑通过，但不替代真实联通证据。

## v0.1.1-lightweight 增补（2026-08-09，T14 整改关闭）

基线 `v0.1.0-lightweight` 不变；本增补修复 T14 独立验收的三项 Important 并补齐真实联通证据，最终 SHA 以标签 `v0.1.1-lightweight` 为准。

| 整改项 | 内容与证据 | 状态 |
| --- | --- | --- |
| R01 `.env` 不生效 | 容器默认组合根加载项目根 `.env`（进程环境优先，依赖 `python-dotenv` 入锁）；干净克隆仅填 `.env` 即装配 import/query/lint 三服务；同时封堵 pydantic 错误文本内嵌 Key 的泄漏路径（`tests/unit/test_container_dotenv.py` 4 项） | ✅ |
| R02 runbook 与真实契约冲突 | [dify-import.md](../runbook/dify-import.md) 按代码唯一权威重写（authority_level / severity / side / effective_rules / 信封路径），六份版本化 fixture + `tests/unit/test_dify_runbook_fixtures.py`（14 项，含枚举变异反证）防漂移 | ✅ |
| R03 真实 Dify 全流程未验证 | 干净克隆（`uv sync --frozen` + `.env` 三互异 Key，无手工代码改动）对真实 Dify 云工作流跑通全链路：真实导入（run `a9db4486-…`，1 条 conflict）→ 发布前真实查询（run `ef9f2db9-…`，命中 LLD-724_1）→ 真实自检（run `7ce68fe5-…`，blocking 双侧证据）→ 决定→变更单→审批→发布 LLD-724_1→LLD-724_2 → 发布后真实查询命中新版（run `b6a1e4b6-…`，引用 `CIT-BASE-LLD-724_2-01`）→ 追溯主链 6 节点 `missing_links=[]`；全链路无重试、无错误码。证据存档 [../qa/dify-live-e2e-2026-08-09.json](../qa/dify-live-e2e-2026-08-09.json)（脱敏无 Key），实录表见 dify-import.md 第七节 | ✅ |
| 传输编码（R03 附属修复） | Dify 开始节点不支持数组变量（实测）：数组在线路编码为 JSON 字符串（`encode_for_dify_transport`），工作流首代码节点 `json.loads` 还原；mock 网关以 `decode_for_dify_transport` 同构复现（`tests/unit/test_dify_transport_encoding.py` 4 项） | ✅ |
| 平台陷阱入册 | 节点 ID 连字符导致 `{{#节点.变量#}}` 不插值（实测）、max_tokens 缺省导致输出截断、导入 content 须逐字命中原文、查询有相关卡时须逐字引用——全部写入 dify-import.md 第四节 | ✅ |
| O01/O02 | 删除误导的 `.streamlit/secrets.toml.example`（应用从不读 st.secrets）；README/runbook 区分「启动证据 vs 业务流程证据」 | ✅ |

增补验证记录（2026-08-09，仓库根目录）：

```text
uv run pytest                      → 752 passed, 0 failed
uv run ruff check src scripts tests streamlit_app.py          → All checks passed!
uv run ruff format --check src scripts tests streamlit_app.py → 166 files already formatted
uv run python scripts/reset_demo.py --snapshot initial        → RESET_OK
uv run python scripts/validate_data.py                        → VALIDATION_OK baseline=LLD-724_1
```
