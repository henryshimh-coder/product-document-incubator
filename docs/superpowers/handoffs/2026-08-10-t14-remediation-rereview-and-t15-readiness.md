# T14 整改复核与 T15 准入报告（第二轮）

> 复核日期：2026-08-10  
> 复核角色：独立 reviewer  
> 分支：`feat/lightweight-t01`  
> 整改前 HEAD：`5bcc3d7`  
> 本轮 HEAD：`68c7794a5f8310144a5bc9e09ffdf5dc950a7cff`  
> 补丁标签：`v0.1.1-lightweight`  
> 结论：**T14-R01、T14-R02 已通过；T14-R03 的整改方真实联通证据完整且与代码 SHA 一致，但独立 reviewer 的外部 Dify 复跑因缺少明确数据外发授权未执行。当前暂不签署 T15 准入。**

## 1. 结论摘要

本轮整改解决了上一轮发现的本地实现问题：

- 默认容器可以从项目根 `.env` 装配三个实时服务；空 Key 保持本地模式，重复 Key 被拒绝且异常不泄露 Key。
- Dify runbook 的 authority、Query effective rules、Lint severity/evidence side 已与真实代码契约统一。
- 六份版本化 fixture 与 14 项契约/变异测试已建立。
- Dify 不接受数组型开始节点变量的问题已在传输边界解决：数组编码为 JSON 字符串，工作流解析节点还原。
- 新标签 `v0.1.1-lightweight` 正确指向整改 HEAD。

整改方提交了真实 Dify 全链记录：四次实时调用、发布前后版本、双侧 Lint 证据、发布和六节点追溯均完整；记录中的 SHA `e6b95cb` 是当前 HEAD 的祖先，该 SHA 之后唯一生产代码变化是增加供 mock 测试使用的 `decode_for_dify_transport()`，不改变真实请求路径。

独立 reviewer 在新的干净克隆中已确认 `.env` 三个 Key 均存在、默认容器三服务均装配，但发起真实调用会将演示风险材料、基线卡片、引用和查询/自检内容发送到外部 Dify，并消耗外部模型额度。本轮“已整改”没有明确授权该次数据外发，安全审批因此拒绝执行。独立 reviewer 不绕过该限制。

## 2. 整改项逐项判定

### T14-R01：通过

独立复核结果：

- `python-dotenv` 已加入 `pyproject.toml` 与 `uv.lock`。
- `build_container()` 的默认路径从 `project_root/.env` 加载配置，进程环境变量优先。
- 显式 `environ` 的测试/嵌入路径不读取 `.env`。
- 干净克隆中只放置 `.env`、不手工 export，实测：

```text
services True True True
```

- 四项单元测试覆盖有效 `.env`、空 Key、本地模式、重复 Key 和异常/repr 不泄露。
- 当前跟踪文件扫描未发现三个真实 Key：`tracked_secret_matches []`。

### T14-R02：通过

独立复核结果：

- Ingest `authority_level` 使用四个业务权威枚举，不再混用 L1/L2/L3 安全级别。
- Query `effective_rules` 明确为可信卡片 ID，并校验返回引用与卡片来源引用相交。
- Lint `severity` 与 evidence `side` 已修正为代码真实枚举。
- 六份 input/output fixture 均通过对应 Pydantic 模型。
- Query 与 Lint application 语义断言已覆盖。
- 五类旧枚举及规则文本变异均有失败反证。

本轮专项复跑：

```text
test_container_dotenv.py
test_dify_runbook_fixtures.py
test_dify_transport_encoding.py
→ 22 passed
```

### T14-R03：整改方证据通过内容审查，独立外部复跑待授权

已验证的证据：

- `docs/qa/dify-live-e2e-2026-08-09.json` 不含 API Key。
- 证据 SHA：`e6b95cbdb7231483385c3b73e0d75e7073a10150`，确为当前 HEAD 祖先。
- 真实 Ingest：HTTP 成功、realtime、1 条 conflict，具有 workflow run ID。
- 发布前 Query：命中 `LLD-724_1`、可信卡片 ID、可信引用。
- 真实 Lint：blocking、`current_baseline` + `challenging_source` 双侧证据。
- 本地决定、审批、发布：`LLD-724_1 → LLD-724_2`。
- 发布后 Query：命中 `LLD-724_2` 与新版引用。
- Trace：source→knowledge→issue→decision→change→baseline 六节点，`missing_links=[]`。
- 证据文件 SHA-256：`259eadcdbcc8b17a6080ca331aabc40520131f19f0334a89a670fb3e5fc17711`。

尚未完成的独立动作：

- reviewer 自己发起四次真实 Dify 调用并获得一组新的 run ID。
- 原因不是代码失败，而是外部数据发送需要用户明确授权。

## 3. 本轮独立门禁结果

### 3.1 干净克隆

从 `v0.1.1-lightweight` 克隆到独立目录：

```text
/private/tmp/t14-remediation-rereview.ZUH3cp/fresh
```

结果：

```text
uv sync --frozen → 59 packages installed
reset_demo initial → RESET_OK
validate_data → VALIDATION_OK baseline=LLD-724_1
默认容器读取 .env → ingest/query/lint = True/True/True
```

### 3.2 自动测试与覆盖率

```text
黄金 + E2E + 安全专项：83 passed in 6.28s
全量：752 passed in 25.30s
domain + application：2594 statements，119 missed，95.41%
测试 warning：0
```

### 3.3 静态与依赖门禁

```text
uv lock --check：Resolved 65 packages
ruff check：All checks passed
ruff format --check：166 files already formatted
compileall：通过
git diff --check：通过
标签 v0.1.1-lightweight → 当前 HEAD 68c7794
```

## 4. 独立真实复跑的授权范围

为关闭最后的外部验证条件，需要用户明确授权以下动作：

1. 在 `/private/tmp` 的 `v0.1.1-lightweight` 干净克隆中运行，不修改当前仓库运行态。
2. 使用项目根 `.env` 中已配置的三个 Dify Key，但不输出、记录或提交 Key。
3. 向 `.env` 指向的 Dify 发送仓库内置的脱敏模拟材料及治理上下文：
   - `tests/fixtures/sources/risk_opinion.md`；
   - 当前模拟基线卡片、来源引用和问题“当前目标客群是什么？”；
   - 自检所需的模拟基线/挑战来源摘录与本地确定性发现。
4. 发起四次真实调用：Ingest、发布前 Query、Lint、发布后 Query；会消耗相应 Dify/模型额度。
5. 决定、审批、发布与追溯只发生在临时克隆本地数据中。
6. 输出仅包含状态码、耗时、run ID、版本、引用 ID、严重度和追溯节点，不包含 Key 或未脱敏材料。

## 5. T15 准入判定

```text
T14-R01：通过
T14-R02：通过
T14-R03 整改方证据：通过内容审查
T14-R03 独立实时复跑：待明确授权
本地 Critical 未关闭：0
本地 Important 未关闭：0
T15 准入签署：暂缓
```

用户明确授权第 4 节的外部调用后，reviewer 将完成最后一次真实全链复跑；若成功且演练后重置/校验保持通过，即可签署 T15 准入。
