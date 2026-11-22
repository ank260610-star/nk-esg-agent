# 闸门 A（Task 1）/ 闸门 B（Task 2 解析）实测决策记录

记录时间：2026-11-22（北京时间）｜记录人：智能体技术成员
更新：2026-11-22 真机工具调用链与权限验证完成后由「未通过」改判为「通过」。
更新：2026-11-22 完成 Task 2 通用坐标还原引擎，真实比亚迪 PDF 六指标 6/6 命中（见文末「闸门 B」节）。

## 闸门 B（Task 2：PDF 解析与检索）— 解析部分通过

**结论：闸门 B 解析部分通过。** 真实比亚迪 2024 可持续发展报告（SHA256 `8dfceab...3c93b1dd`，155 页）实测：
- 文字层完好（20/20 抽样页有文字），无需 OCR。
- `extract_tables()` 对目标页返回 0 张表——报告为「文字排版伪表格」，改用通用 `extract_words()` 坐标还原引擎（`parsing.py`）。
- 六项指标 gold 值 **6/6 全部命中**，年度列不串行、单位不混用、`/` 占位保留、脚注 21 正文定位到 p127：
  - E01/E02/E03 → PDF p127（印刷 126）；E04 → p125（印刷 124）；E05/E06 → p126（印刷 125）。
- 解析引擎不硬编码页码/数值：指标别名外置到 `rules/metric-dictionary.json`，换报告优先改配置。
- 全量测试：契约 16 + 运行时 27 + Task 2 新增 24 = 全绿。

诚实标注的边界（不影响闸门 B 数值定位结论）：
- 报告为左右双栏混排，同一 y 行含左栏治理数据与右栏环境数据；坐标还原在**数值归属年度列、页码、脚注**上正确，但个别**行标签↔数值对的栏位归属**仍可能跨栏错位（如 p125 的左栏"商业道德违规事件"与右栏能源值同列显示）。数值 gold 命中不受影响，但完整的 row_label 精确绑定列为后续 Task 3 精修项。
- 本期仅验证一份报告；通用性是设计目标，待第二份报告验证后才可声称「通吃伪表格」。
- 检索层已实现别名召回/Top-5/脚注关联，但尚未用标注查询集实测 Top-5 命中率（闸门 B 检索部分待补）。

## 结论（先看这里）

**闸门 A：通过。** 已用真实 DeepSeek API（密钥由用户提供，未落盘到任何提交文件）跑通
`业务触发 → Python SDK → 模型真实工具调用 → TS 插件 → 本地 FastAPI → 结构化结果 → SDK 结束`，
并取得 tool 事件 ↔ HTTP 请求的脱敏对应证据。权限红线（Shell/网络/子 Agent 单调拒绝、
恢复会话仍生效、白名单工具仍可用）全部实测通过。

证据文件（脱敏，不含密钥/令牌原文）：
- `D:\esg-agent-runtime\gate-a-evidence\gate_a_evidence.json`（核心链路）
- `D:\esg-agent-runtime\gate-a-evidence\permission_evidence.json`（权限验证）
- 复现脚本：`scripts/smoke_gate_a.py`、`scripts/verify_permission.py`、`scripts/verify_deny_result.py`、`scripts/probe_tools.py`

## 一、离线部分（有测试证据）

| 项 | 证据 | 结果 |
|---|---|---|
| 源码就位 | codex 分支快照 57 文件（tar.gz 下载，Git 初始化并提交 `32352b4`） | ✅ |
| 16 项契约基线 | `unittest discover -s tests -p test_contracts.py` | ✅ 16 passed |
| 权限/宿主测试 | `pytest tests/test_runtime_access.py tests/test_harness_runner.py` | ✅ 11 passed |
| TS 插件白名单测试 | `npm test` | ✅ 3 passed（含客户端路径脱敏测试） |
| TS 插件编译 | `npm run build`（tsc） | ✅ 通过 |

## 二、真机部分（2026-11-22 实测）

### 2.1 核心链路（smoke_gate_a.py）

| 证据 | 实测值 |
|---|---|
| `finish_reason` | `completed` |
| 模型最终答案 | "调用 `get_task_results` 后返回的 revision 数字是 **7**。" |
| `tool/call` 事件 | seq=17, name=`get_task_results`, arguments=`{}` |
| 本地 HTTP | `POST /agent/tool`, tool=`get_task_results`, has_token=true |
| 时序对应 | tool/call ts=...178007 → HTTP ts=...178025，相差 18ms，事件与请求一一对应 |
| 后端数据 | mock_task_001, revision=7（契约样例，非生产数据） |

### 2.2 权限红线（verify_permission.py / verify_deny_result.py）

| 验证项 | 实测结果 |
|---|---|
| `pwsh` 执行 `echo PWNED` | ✅ 被拒，`tool/result` 含 `FORBIDDEN_TOOL:pwsh not in ESG agent allowlist`，PWNED 未执行 |
| `web_fetch https://example.com` | ✅ 被拒，`FORBIDDEN_TOOL: web_fetch` |
| 恢复会话后再调 shell | ✅ 仍被拒（同一单调 guard，resume 后不失效） |
| 白名单 `get_task_results` | ✅ 仍可用（revision=7） |
| 判定 | `all_checks_pass: true` |

**实现机制**：插件 `src/index.ts` 用 `ctx.tools.guard()` 注册全局单调拒绝——返回 reason 即 deny，
任何 `tools/pre-execute` listener 都无法翻案；白名单集合 `ALLOWED_TOOLS` 闸门 A 阶段只含
`get_task_results`，完整 P0 再按 `contracts/tools.json` 逐个放开其余 9 个。
注意：模型仍会发起 `tool/call` 事件（可见工具清单约 30 个），但执行在 runtime 层被 guard 拦截；
权威证据是 `tool/result` 里的 FORBIDDEN，callId 与 tool/call 一一对应。

### 2.3 权限预设实测

- `probe_tools.py` 的 `request/header` 事件确认：会话 system-reminder 显示
  "Current DSH file policy: read-only"、"Approval prompts are enabled"——
  sdk profile 的 `read-only` 命名预设确实生效（此前二轮报告中"read-only 不是预设"的判断作废，见 GitHub 清单 4.2）。
- 但 read-only 只管文件系统；**业务 HTTP 权限靠 guard + 任务令牌**，不依赖沙箱（队长强调的边界已遵守）。

## 三、环境坑与解决（供复现）

| 坑 | 解决 |
|---|---|
| git clone 被本机代理拦断 | 用官方 codeload tar.gz 下载后 `git init` |
| PyPI 默认源查不到 SDK | 官方包名 `deepseek-harness-sdk` 是 rc 版（`--pre`），第三方同名 `deepseek-harness` 不是它 |
| runtime-bin wheel 86MB 走代理 20KB/s | 阿里云 PyPI 镜像 15 秒下完，SHA256 与官方一致 |
| 真机首跑报 `koffi.node` 找不到 | wheel 打包遗漏的原生 FFI 模块；从 npmmirror 下 `@koromix/koffi-win32-x64` 放到 exe 硬编码路径 `D:\snapshot\node\node_modules\...\` |
| `download_primary_runtime()` 反复超时 | 非必需；wheel 自带 single-file exe 已能启动，缺的只是 koffi 一个文件 |
| `dsh plugin add` 需要 pnpm | 用 npm 走国内镜像装 pnpm 10.34.9，再 `dsh plugin --profile sdk add file:<绝对路径>` |
| 插件装了但模型看不到工具 | 需在 profile `cordis.patch.yml` 用 `- insert: [{id, name}]` 显式加载（插件没声明 `dsh.bundle`，仅作为 plain dependency 不会被自动加载） |

## 四、明确的工程决策

1. **先写失败测试再实现**：新增 `tests/test_runtime_access.py`、`tests/test_harness_runner.py`，红灯后写实现，未改原 16 项契约测试。
2. **最小实现只做读路径**：后端仅 `get_task_results` 契约信封 + 任务令牌隔离，未实现其余 9 工具，未把 mock 当生产数据。
3. **默认拒绝白名单**：后端 `Settings.allowed_agent_tools` 只含 `get_task_results`；插件侧 `ctx.tools.guard()` 单调拒绝一切非白名单工具。
4. **任务隔离**：A 令牌访问 B 任务返回 403，错误信封 `task_id`/`revision` 为 null。
5. **人工复核隔离**：Agent 令牌访问 `/tasks/{id}/review` 返回 403，复核只许独立用户会话 + CSRF。
6. **状态诚实**：`AgentRunStore.complete()` 拒绝空答案；SDK 异常只写 failed。
7. **两处待办已修复**：① `BusinessClient` 改走 `/agent/tool`（服务端按令牌解析 task_id，模型不填 ID）；② 插件 `output.schema` 按 `get_task_results_output` 的 oneOf 信封收紧（property 级 `required`、`oneOf` 表达可空、`additionalProperties:false`）。

## 五、已知限制（诚实标注）

- 后端是内存仓储（`InMemoryTaskStore`），非 SQLite 事务；revision 递增、幂等、审计日志留待 Task 2+。
- 模型可见工具清单仍含约 30 个内置工具（pwsh/write/web/subagent 等），只是执行被 guard 拦截；
  若要让工具对模型**不可见**（presentation 层收窄），需用 `ctx.tools.restrict()` 作用于 agent scope——
  闸门 A 时间所限未做，属 P1，不影响"权限边界已验证"的结论（执行层已单调拒绝）。
- 烟测用 mock_task_001 契约样例数据，不是比亚迪真实报告；真实 PDF 解析属 Task 2/闸门 B。
- `koffi.node` 放到 `D:\snapshot\...` 是绕过 wheel 打包缺陷的权宜之计；换机器需重做此步。
- 模型 `deepseek-v4-flash` 为实测可用版本；单价/币种/峰谷规则仍以官方页为准，未写入预算承诺。
