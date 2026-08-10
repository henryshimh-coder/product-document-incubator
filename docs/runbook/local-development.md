# 本地开发手册

## 环境准备

- Python `>=3.11, <3.13`（`uv` 会自动选择受管解释器）
- `uv`（依赖全部锁定在 `uv.lock`）

```bash
uv sync --frozen          # 严格按锁文件安装（含 dev 组：pytest / pytest-cov / ruff）
cp .env.example .env      # 本地开发配置；Dify Key 留空时应用以本地治理功能启动
uv run python scripts/bootstrap_demo.py
uv run python scripts/validate_data.py    # 期望输出 VALIDATION_OK baseline=LLD-724_1
```

## 启动应用

```bash
uv run streamlit run streamlit_app.py --server.headless true
```

## 测试

```bash
uv run pytest                                   # 全量 730 项
uv run pytest tests/golden tests/e2e tests/security -q   # 黄金 + E2E + 安全专项（83 项）
uv run pytest -q --cov=src/domain --cov=src/application \
  --cov-report=term --cov-fail-under=85         # 覆盖率门禁（当前 95.40%）
```

## 静态检查

```bash
uv run ruff check src scripts tests streamlit_app.py
uv run ruff format --check src scripts tests streamlit_app.py
```

如实说明：以上为仓库既定检查范围（163 个文件）。仓库级 `ruff check .` 会命中 `.superpowers/` 下留存的验收证据脚本，不属于生产代码门禁。

## 配置与环境变量

- `config/app.yaml`：项目标识（`LLD`）、超时（导入 60s / 查询 30s / 自检 60s）、缓存精确键匹配、发布需人工审批。
- `config/schema.yaml`：对象 / 状态 / 关系类型字典；实时自检部署必须显式声明 `lint_input_contract_version: "2.0"`（仓库已含）。
- `config/lint_rules.yaml`：确定性自检规则。
- `.env` 模板见 `.env.example`；Streamlit secrets 模板见 `.streamlit/secrets.toml.example`。

## 目录与数据约定

- `data/demo_snapshots/{initial,frozen}`：已跟踪的演示快照，是一键重置与干净环境重建的唯一事实来源。
- `data/obsidian_vault/`、`data/source_archive/`、`data/local_state/`：运行态，不入库（`.gitignore`），由 `bootstrap_demo.py` 或 `reset_demo.py` 再生。
- 演示操作与故障恢复见 [demo-operation.md](demo-operation.md) 与 [recovery.md](recovery.md)。
