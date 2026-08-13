# 产品文档孵化器 2.0

面向 Owner 产品经理的本地优先产品文档工作台：一个项目一套 Wiki-LLM 目录，支持原始材料不可变归档、候选方案孵化、Owner 发布、当前 Markdown 下载和跨项目标题结构建议。1.x 的“产品智策”比赛材料及演示快照作为历史资产保留。

- 版本：`v2.0`（多项目产品文档孵化首期）
- Python：`>=3.11, <3.13`
- 依赖管理：`uv`（锁文件 `uv.lock`）

## 快速开始（干净设备）

```bash
uv sync --frozen
cp .env.example .env            # 按需填入三个 Dify Workflow Key，见下「外部模型配置」
uv run python scripts/bootstrap_demo.py   # 可选：初始化 1.x 演示数据
uv run python scripts/migrate_lld_to_v2.py --source-root . --library-root "$HOME/Documents/产品文档孵化器项目库" --dry-run
uv run streamlit run streamlit_app.py --server.headless true
```

浏览器打开 `http://localhost:8501`。首次进入“项目中心”完成 Owner 与项目库设置，随后新建项目即可自动建立本地 Wiki-LLM 文件结构。

首次执行 `uv sync --frozen` 需要网络或预热好的 uv 缓存；本交付不是离线安装包。

## 外部模型配置

应用在容器构建时自动加载项目根 `.env`（模板 `.env.example`；已存在的进程环境变量优先，不会被 `.env` 覆盖）：

| 变量 | 用途 |
| --- | --- |
| `DIFY_BASE_URL` | Dify API 地址，默认 `https://api.dify.ai/v1` |
| `DIFY_DOCUMENT_API_KEY` | 产品方案孵化与结构建议 Workflow Key |

**未配置 Key 时应用仍可启动**，Owner 仍可建项目、归档材料和查看已发布文档；孵化与结构建议需要该 Key。历史 1.x 三个 Workflow 的配置说明仍见 [docs/runbook/dify-import.md](docs/runbook/dify-import.md)。

## 常用操作

```bash
.venv/bin/python scripts/migrate_lld_to_v2.py --source-root . --library-root "$HOME/Documents/产品文档孵化器项目库" --dry-run
.venv/bin/python -m pytest -q
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
data/demo_snapshots/  1.x 已跟踪演示快照（历史资产）
tests/                unit / integration / golden / e2e / security 测试
docs/runbook/         操作手册
docs/delivery/        交付清单
docs/qa/              测试报告与 UI 验收证据
```

## 交付与质量证据

- 交付清单：[docs/delivery/2026-08-24-checklist.md](docs/delivery/2026-08-24-checklist.md)
- 测试报告（730 项全过、黄金指标、E2E / 安全 / 三次重置演练）：[docs/qa/test-report-2026-08-24.md](docs/qa/test-report-2026-08-24.md)
- 1440×1024 UI 逐页验收（真实浏览器截图）：[docs/qa/ui-acceptance-1440x1024.md](docs/qa/ui-acceptance-1440x1024.md)

## 首期限制

只支持单 Owner、UTF-8 Markdown 下载和手动触发结构建议；不提供项目删除、多人协作、DOCX/PDF 导出或自动文件监听。
