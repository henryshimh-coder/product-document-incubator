# 产品智策（轻量交付版）

面向产品团队的轻量知识治理演示系统：以 Streamlit 单页应用承载「基线 → 导入 → 查询 → 自检 → 决定 → 变更单 → 审批 → 发布 → 追溯」全链路，外部模型侧为三个独立治理的 Dify Workflow（导入 / 查询 / 自检），本地侧为 SQLite + Markdown Vault + 来源归档的可审计状态。

- 版本：`v0.1.0-lightweight`（2026-08-24 轻量交付）
- Python：`>=3.11, <3.13`
- 依赖管理：`uv`（锁文件 `uv.lock`）

## 快速开始（干净设备）

```bash
uv sync --frozen
cp .env.example .env            # 按需填入三个 Dify Workflow Key，见下「外部模型配置」
uv run python scripts/bootstrap_demo.py   # 初始化演示数据，输出 BOOTSTRAP_OK
uv run python scripts/validate_data.py    # 校验演示环境，输出 VALIDATION_OK
uv run streamlit run streamlit_app.py --server.headless true
```

浏览器打开 `http://localhost:8501`。以上流程已在一台干净目录（仅含仓库跟踪文件）实测通过，无需任何手工代码改动。该实测是**启动证据**（安装 → 初始化 → 校验 → HTTP 200）；接入真实 Dify 后的**完整业务流程证据**见 [docs/runbook/dify-import.md](docs/runbook/dify-import.md) 第七节，交付清单对两类证据分别记录。

首次执行 `uv sync --frozen` 需要网络或预热好的 uv 缓存；本交付不是离线安装包。

## 外部模型配置

应用在容器构建时自动加载项目根 `.env`（模板 `.env.example`；已存在的进程环境变量优先，不会被 `.env` 覆盖）：

| 变量 | 用途 |
| --- | --- |
| `DIFY_BASE_URL` | Dify API 地址，默认 `https://api.dify.ai/v1` |
| `DIFY_INGEST_API_KEY` | 导入 Workflow Key |
| `DIFY_QUERY_API_KEY` | 查询 Workflow Key |
| `DIFY_LINT_API_KEY` | 自检 Workflow Key |

三个 Key 必须互不相同（启动时校验，Key 不进入异常文本或日志）。**未配置 Key 时应用仍可启动**，但仅提供本地治理功能（首页 / 决定 / 审批 / 发布 / 追溯）；导入、查询、自检的实时与缓存能力需要 Key。三个 Workflow 的输入输出契约与导入步骤见 [docs/runbook/dify-import.md](docs/runbook/dify-import.md)。

## 常用操作

```bash
uv run python scripts/reset_demo.py --snapshot initial   # 重置到初始基线 LLD-724_1
uv run python scripts/reset_demo.py --snapshot frozen    # 重置到冻结演示态（含冻结缓存）
uv run python scripts/validate_data.py                   # 校验 Manifest / SQLite / 缓存 / 归档
uv run pytest                                            # 全量自动测试（730 项）
```

详细操作手册见 [docs/runbook/](docs/runbook/)：

- [local-development.md](docs/runbook/local-development.md) — 本地开发、测试与静态检查
- [demo-operation.md](docs/runbook/demo-operation.md) — 演示流程操作（重置、快照、页面动线）
- [recovery.md](docs/runbook/recovery.md) — 故障恢复与常见问题
- [dify-import.md](docs/runbook/dify-import.md) — 三个 Dify Workflow 的创建 / 导入与联通性验证

## 目录结构

```text
streamlit_app.py      应用入口
config/               应用、自检规则与 schema 配置
src/                  domain / application / infrastructure / ui 分层代码
scripts/              演示数据初始化、校验、快照与重置脚本
data/demo_snapshots/  已跟踪的 initial / frozen 演示快照（可再生的运行态不入库）
tests/                unit / integration / golden / e2e / security 测试
docs/runbook/         操作手册
docs/delivery/        交付清单
docs/qa/              测试报告与 UI 验收证据
```

## 交付与质量证据

- 交付清单：[docs/delivery/2026-08-24-checklist.md](docs/delivery/2026-08-24-checklist.md)
- 测试报告（730 项全过、黄金指标、E2E / 安全 / 三次重置演练）：[docs/qa/test-report-2026-08-24.md](docs/qa/test-report-2026-08-24.md)
- 1440×1024 UI 逐页验收（真实浏览器截图）：[docs/qa/ui-acceptance-1440x1024.md](docs/qa/ui-acceptance-1440x1024.md)

## 已知限制

1. T13 验收的外部模型侧为本地 mock 网关；真实 Dify 联通性按 [dify-import.md](docs/runbook/dify-import.md) 演练验证。
2. 发布后可比对材料只剩小基线时，全量自检会按 25% 出站覆盖率预算 fail-closed（`OUTBOUND_COVERAGE_EXCEEDED`），属治理设计行为；先导入新材料再自检。
3. 首页眉题上半部被 Streamlit 框架工具栏裁切、右上角 Deploy 按钮为框架原生元素，均非产品 UI 缺陷。
