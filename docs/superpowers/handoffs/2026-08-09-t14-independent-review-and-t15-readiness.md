# T14 独立验收与 T15 准入报告

> 复核日期：2026-08-09  
> 复核角色：独立 reviewer  
> 分支：`feat/lightweight-t01`  
> T14 基线：`e79700e`  
> T14 HEAD：`5bcc3d7864da4e6328826baf54e66f8e5a995784`  
> 交付标签：`v0.1.0-lightweight`  
> 结论：**T14 暂不通过，暂不准许进入 T15。自动门禁与本地干净环境启动通过，但仍有 3 项 Important 交付缺口。**

## 1. 结论摘要

T14 已完成 README、四份 runbook、交付清单、依赖锁验证、干净目录安装启动、完整自动测试和交付标签。独立 reviewer 可以复现：

- `uv lock --check` 通过，解析 64 个包；
- 从标签创建的独立克隆可 `uv sync --frozen`；
- 初始化、数据校验和 Streamlit HTTP 200 通过；
- 独立克隆中完整成功 E2E 为 5 passed；
- 专项 83 passed，全量 730 passed，领域 + application 覆盖率 95.40%；
- Ruff、格式、compileall、`git diff --check` 均通过；
- 标签正确指向 T14 HEAD。

但 T14 的核心验收证据是“新工程师只按 README 和 runbook 可在干净设备启动、重置并跑通完整流程”。当前证据只证明应用能启动到 HTTP 200；按照 README 填写 `.env` 后，应用仍读不到三个 Dify Key。同时 Dify runbook 的输入输出枚举与真实 Pydantic / application 契约存在多处冲突，且手册自己标记为“交付前必做”的真实 Dify 联通演练明确没有执行。

这些缺口会直接阻断 T15 的实时 Ingest / Query / Lint 十次性能采样，因此不能作为 Minor 或留待 T15 再处理。

## 2. T14 计划逐项判定

| 计划项 | 独立结果 | 判定 |
| --- | --- | --- |
| Step 1：`uv lock --check` | 64 packages，退出码 0 | 通过 |
| Step 2：全新环境启动 | 独立克隆完成 sync、bootstrap、validate、HTTP 200 | 部分通过：`.env` 配置不生效，实时服务未装配 |
| Step 3：13 项交付清单 | 文件和条目齐全 | 部分通过：Dify 契约错误；安全复核人为角色级记录 |
| Step 4：重置、校验、全量测试 | `RESET_OK`、`VALIDATION_OK`、730 passed | 通过 |
| Step 5：交付提交与标签 | 标签存在并指向 `5bcc3d7` | 通过 |
| 最终验收证据：干净设备跑通完整流程 | 本地 mock E2E 可跑；真实 Dify 主线未跑 | 未通过 |

## 3. 本轮独立验证证据

### 3.1 标签与依赖锁

```text
git rev-list -n 1 v0.1.0-lightweight
→ 5bcc3d7864da4e6328826baf54e66f8e5a995784

.venv/bin/uv lock --check
→ Resolved 64 packages
```

### 3.2 干净目录启动

独立 reviewer 从标签克隆到：

```text
/private/tmp/t14-independent-review.XO252y/fresh
```

随后执行 T14 文档规定的安装与启动流程：

```text
uv sync --frozen
→ 创建独立 .venv，安装 58 个当前平台所需包

cp .env.example .env
bootstrap_demo.py
→ BOOTSTRAP_OK baseline=LLD-724_1

validate_data.py
→ VALIDATION_OK baseline=LLD-724_1

streamlit --server.headless true --server.port 8872
→ /_stcore/health HTTP 200
```

判定：无手工改代码可以完成安装、初始化、校验和启动。

### 3.3 干净目录完整 mock E2E

```text
RESET_OK snapshot=initial
VALIDATION_OK baseline=LLD-724_1
tests/e2e/test_full_success.py
→ 5 passed
```

判定：本地确定性 mock 主流程可交付；该证据不能替代真实 Dify 联通。

### 3.4 专项、全量与覆盖率

```text
tests/golden + tests/e2e + tests/security
→ 83 passed in 5.90s

全量 + domain/application 覆盖率门禁
→ 730 passed in 25.21s
→ 2587 statements, 119 missed
→ Total coverage 95.40%（门槛 85%）
```

本轮输出无 warning。

### 3.5 静态门禁

```text
ruff check：All checks passed
ruff format --check：163 files already formatted
compileall：通过
git diff --check：通过
```

## 4. 必须整改项

### T14-R01（Important）：让 README 的 `.env` 配置路径真实生效

**独立发现**

- `README.md:13,23-32` 指示复制并填写 `.env`，并声称应用读取 `.env` 或 Streamlit secrets。
- `streamlit_app.py:17` 直接调用 `build_container()`。
- `src/application/container.py:321,353-359` 只读取 `os.environ`，代码库没有 `load_dotenv` 或等价加载逻辑。
- 独立克隆的 `.env` 已填写三个互异 Key，并明确清除进程环境变量后调用默认容器，实测：

```text
dotenv_exists True
env_ingest None
services False False False
```

即：README 最主要的配置路径不能装配导入、查询、自检服务。

**修改建议**

二选一，推荐 A：

- 路线 A：增加显式 `.env` 加载（例如 `python-dotenv`），在应用容器构建前加载项目根 `.env`；同步更新 `pyproject.toml` 和 `uv.lock`。
- 路线 B：不承诺应用自动读取 `.env`，将 README / runbook 的启动命令统一改成能够真实注入环境的方式，例如 `uv run --env-file .env streamlit ...`，并覆盖开发、演示、恢复和 Dify 联通流程。

两条路线都必须保持：空 Key 时本地功能可启动、三个 Key 不得写日志、三个 Key 必须互异。

**验收标准**

1. 干净克隆后只执行 README 命令，填写 `.env` 但不手工 `export`，默认 `build_container()` 的 ingest/query/lint 三项服务均已装配。
2. `.env` 留空时三项服务保持不可用，本地 dashboard/决定/审批/发布/追溯可用。
3. 新增自动测试覆盖“`.env` 有效”“空 Key 本地模式”“重复 Key 拒绝”“Key 不进入日志或异常文本”。
4. `uv lock --check`、`uv sync --frozen`、全量测试继续通过。

### T14-R02（Important）：修正 Dify runbook 与真实契约的冲突，并让示例可执行验证

**独立发现**

`docs/runbook/dify-import.md` 至少存在四处会导致真实工作流失败的契约错误：

| 手册内容 | 真实契约 | 影响 |
| --- | --- | --- |
| Ingest `authority_level(L1-L3)` | `formal_effective / formal_decision / professional_opinion / discussion_reference` | 输入会被 `IngestWorkflowInput` 拒绝 |
| Query `effective_rules` 为“规则文本” | 必须是输入 `effective_cards[].id` 的可信卡片 ID | 应用报 `UNKNOWN_EFFECTIVE_RULE` |
| Lint `severity=critical|major|minor` | `blocking / pending_decision / pending_info` | 输出模型校验失败 |
| Lint evidence `side=baseline|comparison` | `current_baseline / challenging_source` | 输出模型校验失败 |

独立枚举验证结果：

```text
AuthorityLevel L2 → REJECTED
IssueSeverity critical → REJECTED
EvidenceSide baseline → REJECTED
```

仓库现有反证测试也明确证明“规则文本作为 `effective_rules`”会触发 `UNKNOWN_EFFECTIVE_RULE`。

**修改建议**

1. 按 `src/infrastructure/gateways/schemas.py` 和 application 二次校验完整重写三类输入输出表与 JSON 示例。
2. 将六个示例（Ingest / Query / Lint 的 input + output）落为版本化 JSON fixture，不只留在 Markdown 中。
3. 增加文档契约测试：逐个 fixture 经对应 Pydantic 模型校验，并覆盖 Query 的可信卡片 ID、引用回连和 Lint 双侧证据语义。
4. 补充三个 Workflow 的最小节点映射：开始节点变量、模型/代码节点责任、结束节点字段映射、blocking 响应结构及发布取 Key 步骤。

**验收标准**

1. 六个 runbook fixture 均能通过对应 `*WorkflowInput` / `*WorkflowOutput` 校验。
2. Query fixture 中 `effective_rules` 只含输入卡片 ID，引用 ID 与该卡片来源引用相交。
3. Lint blocking/pending_decision 示例同时包含 `current_baseline` 与 `challenging_source` 两侧、至少两个 source。
4. 将任一枚举改回手册当前错误值时，文档契约测试必须失败。
5. 一名未参与开发的工程师只依赖 runbook 能创建或导入三个工作流，并通过下一项真实联通验收。

### T14-R03（Important）：补齐真实 Dify 联通与“干净设备完整流程”证据

**独立发现**

- `docs/runbook/dify-import.md:91-106` 将真实联通演练标为“交付前必做项”。
- T14 实施报告 `:32` 明确记录本机无真实 Key，未执行该演练。
- 交付清单 `:32` 所谓“干净设备验收”只做到 HTTP 200，没有执行导入、查询、自检、决定、审批、发布和追溯。
- 当前进程中四项 Dify 配置均未提供；独立 reviewer 无法补跑真实联通。
- T15 Step 1 要求实时 Ingest / Query / Lint 各采样 10 次，没有已联通的三个 Workflow 就无法开始核心加固。

**修改建议**

1. 准备三个已发布且 Key 互异的真实 Dify Workflow，不把 Key 写入仓库、截图、日志或报告。
2. 在新修订交付候选的干净克隆上，按 README 从零配置并启动。
3. 真实执行：Ingest → Query → Lint → 人工决定 → 变更单 → 审批 → 发布 → 发布后 Query → 追溯。
4. 记录非敏感证据：时间、交付 SHA、三类 workflow run ID、结果模式、基线前后版本、引用、最终追溯节点和错误码；对日志做 Key/材料敏感信息复核。
5. 既有 `v0.1.0-lightweight` 不强制移动；整改完成后建议建立新的附注补丁标签（例如 `v0.1.1-lightweight`），让 T15 消费明确、不可变的修订版本。

**验收标准**

1. 干净克隆只按 README/runbook 完成安装、配置、初始化、校验和启动，不改代码。
2. 三个真实 Workflow 均至少成功调用一次，并返回契约有效的结果及独立 run ID。
3. 完整流程从 `LLD-724_1` 发布到 `LLD-724_2`，发布后查询命中新版本，追溯主链完整。
4. 浏览器或操作记录中可见实时/缓存模式、基线版本和引用；不得出现 API Key 或未脱敏材料。
5. 真实演练结束后执行 `reset_demo.py`、`validate_data.py`、专项、全量和静态门禁，全部通过。
6. README、交付清单和实施报告不得再把“HTTP 200”描述为“完整流程通过”；应分别记录启动证据与业务流程证据。

## 5. 非阻断改进项

### T14-O01（Minor）：材料安全复核记录应具有可追责标识

交付清单当前只写“项目负责人（产品经理，演示操作员角色）”。建议在不暴露不必要个人信息的前提下，补充可追责姓名或内部 reviewer ID、精确时间与时区、复核材料清单/哈希、结论及例外项。自动化安全测试是技术证据，不能完全替代交付材料的人审签名。

### T14-O02（Minor）：明确离线安装边界

本轮独立 `uv sync --frozen` 与实施方演练都使用了本机 uv 缓存；这满足锁文件一致与常规干净目录安装，但不证明无网新设备可安装。README 可明确“首次安装需要网络或预热好的 uv 缓存”，避免被误解为离线安装包。

## 6. T15 准入清单

只有以下项目全部满足，才签署进入 T15：

- [ ] T14-R01 关闭：README 配置方式实际装配三个实时服务。
- [ ] T14-R02 关闭：Dify 契约文档、fixture 和自动校验一致。
- [ ] T14-R03 关闭：真实 Dify 三工作流和干净设备完整流程有可复核证据。
- [ ] 新修订标签明确指向整改后的不可变 HEAD。
- [ ] `uv lock --check` 与 `uv sync --frozen` 通过。
- [ ] `reset_demo.py` 与 `validate_data.py` 通过。
- [ ] 专项、全量、覆盖率与静态门禁全部通过。
- [ ] 无 Critical / Important 未关闭。

## 7. 最终判定

```text
T14 Step 1：通过
T14 Step 2：部分通过
T14 Step 3：部分通过
T14 Step 4：通过
T14 Step 5：通过
自动测试：730 passed
专项测试：83 passed
覆盖率：95.40%
Critical 未关闭：0
Important 未关闭：3
T14 验收：不通过
T15 准入：暂缓
```

关闭 T14-R01 至 R03 后，按第 6 节重新执行独立复核；若无 Critical / Important，可签署进入 T15。
