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

## 独立评审整改轮（2026-08-09，branch codex/t14-remediation，base 5bcc3d7）

评审报告（`docs/superpowers/handoffs/2026-08-09-t14-independent-review-and-t15-readiness.md`）判定 T14 不通过、3 项 Important。整改状态：R01/R02 已关闭，R03 阻塞于真实 Dify 登录态（如实记录，见下）。

- **T14-R01 关闭（.env 配置真实生效，路线 A）**：新增 `python-dotenv` 依赖（pyproject + uv.lock，65 packages，`uv lock --check` 通过）；`container._build_stateful_container` 在默认组合根（`environ=None`）从 `project_root/.env` 加载配置且不覆盖进程环境变量，显式 `environ` 注入路径不触碰 `.env`。新增 `tests/unit/test_container_dotenv.py` 四项用例：`.env` 有效装配三项实时服务、空 Key 保持本地模式、重复 Key 拒绝（`ConfigurationError: ... must be distinct`）、Key 不进入异常文本或 settings repr。**测试还抓出一个真实泄漏**：pydantic ValidationError 文本内嵌原始输入（含 API Key），已在组合根包装为只透传校验消息的 `ConfigurationError`（`from None` 切断链）。干净克隆实证：只执行 README 命令、填写 `.env`、显式清除进程变量后默认 `build_container()` 的 import/query/lint 全部装配（True/True/True）。
- **T14-R02 关闭（Dify 契约与示例）**：`docs/runbook/dify-import.md` 按 `schemas.py` 与 application 二次校验完整重写——`authority_level` 四枚举（formal_effective/formal_decision/professional_opinion/discussion_reference）、`severity`（blocking/pending_decision/pending_info）、evidence `side`（current_baseline/challenging_source）、Query `effective_rules` 为可信卡片 ID 且引用相交（附 `UNKNOWN_EFFECTIVE_RULE`/`EFFECTIVE_RULE_CITATION_MISSING` 后果说明）、notices 逐字匹配与 answer 不复述约束、blocking 响应 `data.outputs.result` 结构、三工作流最小节点映射与发布取 Key 步骤。六份示例落为版本化 fixture（`docs/runbook/fixtures/dify/*.json`）；`tests/unit/test_dify_runbook_fixtures.py` 14 用例：六 fixture 模型校验、Query/Lint 语义检查、五个枚举变异（改回 L2/L1/critical/baseline/effective 必失败）、effective_rules 填规则文本的语义变异必失败。
- **T14-R03 阻塞（真实 Dify 联通）**：本机无 `.env` 无任何 Key；WebBridge 真实浏览器实测 `console.dify.ai` 连接被断（ERR_CONNECTION_CLOSED），`cloud.dify.ai` 可达但跳转登录页（用户未登录，GitHub/Google OAuth 不可代操作）；本机 3000/5001/80/8080 无自托管 Dify。**需要用户在浏览器登录 Dify 或提供三个互异 Key 后才能执行**：建工作流 → 干净克隆真实全流程（Ingest→Query→Lint→决定→审批→发布→发布后查询→追溯）→ 非敏感证据（时间/SHA/run ID/版本/引用/追溯节点）→ 演练后全门禁复跑。
- **T14-O01 关闭**：交付清单材料安全复核改为可追责记录——复核人 `shiminghao`（本机交付账号）、精确时间与时区、材料清单与 SHA-256（`71d19c2f...`，与 T12 浏览器证据同源一致）、结论与例外项（无）。
- **T14-O02 关闭**：README 明确「首次 `uv sync --frozen` 需要网络或预热 uv 缓存，本交付不是离线安装包」。
- **附带修正**：应用从不读取 `st.secrets`，删除误导性的 `.streamlit/secrets.toml.example`（`git rm`），README/local-development 配置路径口径统一为「`.env` 自动加载 + 进程环境变量优先」；README 与交付清单明确区分「启动证据（HTTP 200）」与「业务流程证据」，不再混述。
- **整改轮验证**：全量 734 passed（730+4 dotenv）；fixture 契约 14 passed；干净克隆 `.env` 装配实证通过。
