# Task 14 实施报告（8 月 24 日轻量交付封装）

> 执行时间：2026-08-09；分支 `codex/t14`（自 `feat/lightweight-t01` HEAD `e79700e` 切出）。
> T14 准入门禁：独立 reviewer 已正式签署 T13 终验与 T14 准入（`docs/superpowers/handoffs/2026-08-09-t13-final-acceptance-and-t14-admission.md`，签署于 HEAD `e79700e`），无豁免事项。
> 计划中的 `uv run` / `uv sync` 本次均以仓库内可用的 `uv 0.9.26` 原样执行（非等价替代）。

## 计划 Step 逐项执行记录

- **Step 1 锁定依赖**：`uv lock --check` 通过（Resolved 64 packages，锁文件与 `pyproject.toml` 一致，无改动）。
- **Step 2 全新环境启动**：在干净目录 `/tmp/t14_fresh`（`git clone --depth 1` 本分支，仅含跟踪文件，无 `.env`/`.venv`/运行态数据）依次执行计划命令：
  - `uv sync --frozen` → 成功；
  - `cp .env.example .env` → 成功；
  - `uv run python scripts/bootstrap_demo.py` → `BOOTSTRAP_OK baseline=LLD-724_1`；
  - `uv run python scripts/validate_data.py` → `VALIDATION_OK baseline=LLD-724_1`；
  - `uv run streamlit run streamlit_app.py --server.headless true`（端口 8871）→ HTTP 200 后正常停止。
  全程零手工代码改动，满足计划验收证据「新工程师只按 README 与 runbook 可启动」。
- **Step 3 交付清单**：`docs/delivery/2026-08-24-checklist.md`，计划点名的 13 项（源代码版本 / 依赖锁文件 / 配置模板 / 三个 Dify Workflow 导入说明 / SQLite 初始化 / 知识 Vault / 黄金测试数据 / 冻结缓存 / 测试报告 / 演示重置 / 操作手册 / 已知限制 / 材料安全复核人和时间）逐项落实并标注证据。
- **Step 4 交付前最终验证**（仓库根目录）：`reset_demo.py` → `RESET_OK snapshot=initial`；`validate_data.py` → `VALIDATION_OK baseline=LLD-724_1`；`uv run pytest` → **730 passed, 0 failed**。
- **Step 5 交付标签提交**：交付提交含 `README.md`、`docs/runbook/`（四份）、`docs/delivery/`、评审签署 handoff 与本报告；标签 `v0.1.0-lightweight`（附注「August 24 lightweight delivery」）。

## 新增交付物

- `README.md`：项目简介、干净设备快速开始（与 Step 2 实测命令一致）、外部模型配置（三个互异 Dify Key；未配置时仅本地治理功能可用的如实说明）、常用操作、目录结构、已知限制。
- `docs/runbook/local-development.md`：环境准备、启动、测试（730 / 83 专项 / 覆盖率门禁）、scoped ruff 范围如实说明、配置与数据约定。
- `docs/runbook/demo-operation.md`：演示前重置校验流程、initial/frozen 两快照用途、六页面演示主线、覆盖率 fail-closed 与冻结缓存精确键等注意事项。
- `docs/runbook/recovery.md`：一键重置（事务化）、常见错误码处置表（`RESET_LOCKED`/`APP_STATE_LOCKED`/`VALIDATION_FAILED`/`ARCHIVE_CONFLICT`/`CACHE_NOT_FOUND`/`OUTBOUND_COVERAGE_EXCEEDED`/`REDACTION_REQUIRED`/`PUBLISH_SOURCE_INTEGRITY_FAILED` 等）、状态修复原则与日志位置。
- `docs/runbook/dify-import.md`：三个独立治理 Workflow 的创建/导入、互异 Key 配置、`workflows/run` blocking 调用方式、三方输入输出契约（以 `src/infrastructure/gateways/schemas.py` 为准）、交付前真实联通性演练四步。
- `docs/delivery/2026-08-24-checklist.md`：13 项交付清单与最终验证记录。

## 如实记录的边界

1. Dify 联通性演练（dify-import 手册第四节）需要真实 Key，本机无 Key 未执行；T13 验收外部模型侧为 mock 网关的事实已在 README、清单与手册中三处一致声明。
2. `uv.lock` 无改动（锁文件本已一致），计划「Modify: uv.lock」以「验证一致」形式完成。
3. 干净环境演练使用本机已有 uv 缓存安装依赖；未验证无网全新机器场景。
4. 交付标签随交付提交创建；T15 加固（性能采样、fallback_state、演示脚本、预检清单）不在本任务范围。

## 提交与标签

| 对象 | 值 |
| --- | --- |
| 交付提交 | 见 progress.md 台账（`docs: package August 24 lightweight delivery`） |
| 标签 | `v0.1.0-lightweight`（附注提交） |
| 合入 | `codex/t14` → `feat/lightweight-t01` fast-forward |
