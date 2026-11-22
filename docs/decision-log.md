# 闸门 A（Task 1）/ 闸门 B（Task 2 解析）实测决策记录

记录时间：2026-11-22（北京时间）｜记录人：智能体技术成员
更新：2026-11-22 真机工具调用链与权限验证完成后由「未通过」改判为「通过」。
更新：2026-11-22 完成 Task 2 通用坐标还原引擎，真实比亚迪 PDF 六指标 6/6 命中（见文末「闸门 B」节）。
更新：2026-11-22 完成 Task 2 收尾（Top-5 标注实测 6/6 + 乱码/合并单元格案例）与 Task 3（提取/校验/服务端接入），真实 DeepSeek 模型端到端提取比亚迪六项 6/6（见文末「Task 3」节）。

## 闸门 B（Task 2：PDF 解析与检索）— 解析部分通过

**结论：闸门 B 数值定位通过；服务端契约集成仍不完整。** `parse_report` 因涉及 revision 递增与幂等键（契约要求 `mutates_revision=true`），目前仍是函数级可用、未接入 `/agent/tool`——属队长负责的业务仓储范围，不在此处假装完成。`search_evidence` 已接入服务端读路径（见下方自查修复）。

真实比亚迪 2024 可持续发展报告（SHA256 `8dfceab...3c93b1dd`，155 页）实测：
- 文字层完好（20/20 抽样页有文字），无需 OCR。
- `extract_tables()` 对目标页返回 0 张表——报告为「文字排版伪表格」，改用通用 `extract_words()` 坐标还原引擎（`parsing.py`）。
- 六项指标 gold 值 **6/6 全部命中**，年度列不串行、单位不混用、`/` 占位保留、脚注 21 正文定位到 p127：
  - E01/E02/E03 → PDF p127（印刷 126）；E04 → p125（印刷 124）；E05/E06 → p126（印刷 125）。
- 解析引擎不硬编码页码/数值：指标别名外置到 `rules/metric-dictionary.json`，换报告优先改配置。
- 全量测试：契约 16 + 运行时 27 + Task 2 新增 24 = 全绿。

同日自查与修复（用户要求"先自查工程同类问题"后执行）：
- 修复真 bug：`search_evidence` 正文命中数=limit 时，补进来的脚注 21 被 `top[:limit]` 截断丢失 → 改为脚注额外附加不占 limit。回归测试覆盖。
- 修复同源隐患：同指标同年多处取值原被静默拆成孤立 row → 新增 `detect_value_conflicts()`，多值记为 `CROSS_REF_CONFLICT`、同值记为 `CROSS_REF_AGREE`，作为信息保留待业务判定（对应用户"两处取值都是信息"的意见）。
- 修复静默降级：词典加载失败原静默退回最小别名 → 现通过 `DICTIONARY_FALLBACK` issue 显式暴露。
- 补服务端集成缺口：`search_evidence` 已接入 `/agent/tool` 白名单与 FastAPI（此前三个模块是孤岛、脚本级验证被误称为"闸门 B 通过"）。

诚实标注的边界（不影响闸门 B 数值定位结论）：
- 报告为左右双栏混排，同一 y 行含左栏治理数据与右栏环境数据；坐标还原在**数值归属年度列、页码、脚注**上正确，但个别**行标签↔数值对的栏位归属**仍可能跨栏错位（如 p125 的左栏"商业道德违规事件"与右栏能源值同列显示）。数值 gold 命中不受影响，但完整的 row_label 精确绑定列为后续 Task 3 精修项。
- 本期仅验证一份报告；通用性是设计目标，待第二份报告验证后才可声称「通吃伪表格」。
- 检索层 Top-5 命中率已于同日补测：标注查询集 6/6 命中（`tests/test_retrieval_benchmark.py`）；乱码/合并单元格/脚注串位案例已补。
- 发现并修复一处测试隔离 bug：`TestDictionaryFallback` 在 monkeypatch 仍生效时 reload 词典，把全局词典永久留在 fallback 状态，污染后续测试致 Top-5 假性降为 5/6。改用 `monkeypatch.context()` + `finally` 修复。

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

---

## 闸门 C 前置（Task 3：指标提取、内部校验与证据约束）— 真机 6/6

记录时间：2026-11-22。

**结论：Task 3 函数级与服务端集成均通过；真实 DeepSeek 模型端到端提取比亚迪六项 6/6 命中。**

新增模块：
- `schemas.py`：契约 0.2.0 数据结构；`MetricCandidate → MetricRecord` 由服务端补
  `record_id/task_id/revision/review_state/is_current`，模型不得提供。
- `metrics.py`：`validate_candidates()` 内部校验——年份越界、单位口径不符、
  外任务证据整批拒绝、extracted 无值、unknown 无原因、状态值不一致，全部记 issue。
- `extraction.py`：`extract_metrics()` 主入口——revision 变化拒绝提交、幂等 key
  跳过重复模型调用、人工确认记录不覆盖、每组合覆盖检查；模型配置由后端注入。

服务端接入：`extract_metrics` 加入 `/agent/tool` 白名单与 FastAPI 路由；
`task_id` 来自令牌、`revision` 来自任务仓储、证据归属集合来自服务端证据库，
模型不能自选；revision 不符返回 409 `REVISION_CONFLICT`。

测试：`tests/test_metrics.py` 11 项边界 + `tests/test_service_integration.py`
新增 2 项集成；全量 76 passed（含闸门 A 27 + Task 2 + Task 3 + 集成）。

真机验证（`scripts/smoke_extract.py`，真实 DeepSeek `deepseek-v4-flash`）：
- 链路：真实 PDF → pdfplumber 证据块 → 真实模型 → 候选 JSON → `extract_metrics` → gold 对照。
- 结果：**6/6 正确**（E01 10,101,826.20 / E02 0.1300 / E03 3,326,312.49 /
  E04 23,552,569.28 / E05 94,268,627 / E06 55,317,707），model_calls=1。
- 诚实性：模型只拿到通用列序规则 + 完整证据块，**未泄漏 gold 答案**；证据不足的
  组合实测会返回 unknown 而非猜值（调试过程中 E04 曾因证据块缺单位返回 unknown，
  补齐证据后才提取成功）。

**重要数据发现（需队长核对参考答案）**：
- 目标页表头确认为 **2022 / 2023 / 2024 正序**（p125 L3、p127 L2），故 2024 = 每行最后一列。
- 此前侦察稿把 E01 的 `12,341,455`、E02 的 `0.2049` 当成 2024 值——按表头它们实为 **2023**；
  2024 应为 E01 `10,101,826.20`、E02 `0.1300`。本次据表头修正了脚本内 gold 标注。
- E01 同页另有"范围1+2+3"行值 `13,428,138.69`，口径不同不可混入。
- 请队长确认参考答案的年度列映射，避免评分用错年份。

**仍未完成（不在本成员边界）**：`parse_report` 服务端契约（revision 递增 + 幂等键，
属业务仓储）；网页打开 PDF 登记证据 ID（前端 Task 6/7）；复杂表格视觉解析（P2 范围外）。

---

## 闸门 C 前置（Task 4：可比性、跨年计算与情景边界）— 通过

记录时间：2026-11-22。

新增 `comparison.py`（Decimal 精确计算）：
- `parse_decimal`：千分位/尾随零保留；占位符 `/ — 未披露` 返回 None 绝不转 0。
- `compare_records`：比对企业/指标/递增年度/可换算单位/强度分母/边界/方法/复核状态；
  全过才 comparable 并算同比。硬阻断项（单位/分母不符）→ not_comparable；
  边界/方法/复核未确认 → needs_review（不输出同比）；基期 0 → undefined；
  缺失/冲突 → not_comparable。首次自动候选（unreviewed）默认 needs_review。
- `apply_scenario`：白名单确定性公式（WITHHOLD_CERTIFICATE 扣绿证 /
  UNIT_CONVERSION），两年必须用同一变换，缺一年 → ASYMMETRIC_TRANSFORM 拒绝；
  情景值与原披露值隔离，不覆盖、不自动进评分。

服务端接入：`compare_years` 加入 `/agent/tool` 白名单；新增 `RecordStore`，
extract 产物按 record_id 登记；compare 按 ID 取记录并做 task 归属隔离（跨任务 404）。

真机数值验证（与 handoff §6 逐项吻合）：
- 正常同比：E01 -18.15%、E02 -36.55%、E04 +41.95%、E05 +27.72%、E06 +150.68%。
- 绿证对称调整：两年都扣 16,084,592.44 → 21,314,583.28，同比 +32.52%；
  只扣 2024 → rejected / ASYMMETRIC_TRANSFORM（拒绝得出 -0.44% 的错误序列）。
- 算术边界：delta 用 Decimal，+41.95% 对应 delta=6,959,976.84。

测试：新增 `tests/test_comparison.py` 16 项 + 集成 2 项；全量 **94 passed**。
修正一处测试期望值算术错误（delta 6961976.84→6959976.84）与一处夹具问题
（method_text 默认空触发 METHOD_UNCONFIRMED）。

**仍未完成**：Task 5 评分与人工复核、Task 6/7 页面与完整演示链。

---

## 闸门 D 前置（Task 5：版本化评分与人工复核）— 通过

记录时间：2026-11-22。

新增模块：
- `scoring.py`：`score_checks(checks, rulebook)` 严格实现 handoff §4.1 固定公式，
  Decimal 未舍入值：coverage=K/A、reference=100P/K、区间=[100P/A, 100(P+A-K)/A]；
  coverage=1 → final=reference，否则 final=null；A=0 → 全 null/no_applicable_items；
  K=0 且 A>0 → coverage=0、区间[0,100]、参考分 null。五维各自同逻辑。
  校验：候选状态合法性、自动 not_met 无证据拒绝（NOT_MET_NEEDS_EVIDENCE）、
  自动 N/A 拒绝（NA_NEEDS_MANUAL，N/A 仅人工确认）、重复检查拒绝、20 项/权重和=1/
  同维等权/单子条件无 partial。
- `review.py`：`apply_review` 原子复核——理由非空、旧 revision 返回 409、
  Agent/模型令牌一律拒绝（AGENT_CANNOT_REVIEW）、审计事件、revision 递增、
  旧评分 is_current=false；`invalidate_checks_for_metric` 使核心指标变更后关联检查
  失效为 unknown/unreviewed（如 E01 变更影响 C01/Q01/Q02/Y01）。
- `rules_registry.py`：维度权重/检查项归属/指标→检查依赖映射。
- `rules/disclosure-v0.1.json`：20 项试验规则（格式用 JSON 而非 YAML——本机无 PyYAML，
  契约体系与 metric-dictionary 均用 JSON，避免引入依赖；字段严格对齐契约 Rulebook，
  source_refs 全标 unverified、experimental=true、网页须标"试验规则非官方评分"）。

服务端接入：`get_rulebook` / `calculate_score` 加入 `/agent/tool` 白名单；
检查项由服务端 `app.state.checks` 提供、task_id 来自令牌，模型不能自选；
`/tasks/{id}/review` 仅用户会话（x-user header），agent token 403。

**手算用例逐字段核对契约示例 `calculate_score.success.json` 完全一致**：
C01=met、C02=partial、其余 unknown → P=9.375、K=12.5、A=100、
coverage=0.125、参考分75、区间[9.375,96.875]、final=null、label=insufficient_evidence；
维度分 completeness cov=0.5/ref=75/[37.5,87.5]，其余四维 K=0→区间[0,100]/参考分 null。
修正一处展示格式（维度 applicable_weight 无 N/A 时用规则定义的 `0.20` 而非计算值 `0.2`）。

测试：新增 `test_scoring.py` 21 项 + `test_review.py` 9 项 + 集成 3 项；
全量 **127 passed + 30 subtests**。

**仍未完成**：Task 6/7 页面、PDF 定位与同版本导出、完整演示链；
`parse_report` 服务端契约（revision 递增+幂等，属业务仓储）。

---

## Task 6（用户页面 / PDF 定位 / 同版本导出）— 通过

记录时间：2026-11-22。

新增模块：
- `exports.py`：`export_analysis` 同版本三格式（Markdown/CSV/JSON）。CSV 缺失值写空串、`/` 原样保留，
  绝不当 0；公式注入首字符（`= + - @`、制表/回车）加 `'` 转义，但正常负数（如 `-18.15`）保持数值语义。
  `ArtifactStore` 以服务端生成的 `artifact_id` 查表下载，文件名仅取 basename、路径片段/`..` 一律拒绝；
  复核后旧产物 `is_current=false`（仍可下载但页面标「已过期」）。
- `templates/index.html` + `static/style.css`：四区域业务页（报告导入 / 指标与证据 / 评分与未知项 /
  人工复核 / 导出），Jinja2 默认转义不可信报告文本；证据链接指向 `/api/reports/{id}/pdf#page=N`，
  PDF 端点只服务已登记 report_id，不暴露内部路径。
- `main.py`：`POST /tasks/{id}/exports`、`POST /tasks/{id}/agent-runs`（后台线程，不阻塞事件循环；
  mode 限白名单、revision 冲突 409、同任务 active run 互斥）、`GET .../agent-runs/{run_id}`、
  `GET /api/artifacts/{id}`、`GET /api/reports/{id}/pdf`。用户路由拒绝 Agent 令牌；
  本地单用户原型用首页 cookie 会话兼容浏览器表单（无真实认证，服务仍只绑定 127.0.0.1）。
- 复核路由修复两处既有缺口：① 引用了只在 `agent_tool` 内定义的 `store`（用户成功复核路径此前会 500，
  旧测试只覆盖「Agent 令牌被拒」未触发）；② 表单提交不再强制 JSON，revision 字符串转 int。
  复核成功后服务端检查项同步、旧导出失效。

测试：`test_exports.py` 12 项 + `test_web.py` 12 项；契约 16 + pytest **153 passed + 30 subtests**。
新增依赖：`jinja2 3.1.6`、`python-multipart 0.0.34`（阿里云镜像装，清代理）。

**仍未完成**：Task 7 完整 Harness 编排与证据问答（`agent-runs` 目前只落 queued/failed 状态骨架，
未接真实 SDK 会话，不宣称端到端可用）；`parse_report` 服务端契约（业务仓储范围）。
