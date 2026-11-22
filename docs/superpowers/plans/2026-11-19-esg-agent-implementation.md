# 企业环境信息披露质量评估智能体：实现交接文档 v0.3

修订日期：2026-11-20；契约版本：0.2.0；评分规则版本：disclosure-v0.1。主提示词esg-agent-v0.3.0、后端提取提示词metric-extraction-v0.2.0，分别记录版本。

提示词加载、可信上下文与用户输入分离、程序责任及13个待模型验证案例见[提示词使用说明](../../prompts/智能体提示词使用说明.md)。

本文为当前实现交接入口，文件路径保持稳定。已阅读用户提供的《ESG披露质量评估智能体_技术调研报告.md》，将经核查的结论、工程决策与待验证建议分开处理。

> 给接手 Agent：按本文的勾选任务逐项实现、验证并记录结果。当前用户要的是实现交接文档，本轮没有开始搭建。接手后先检查实际代码和 AGENTS.md，再继续；本文不要求任何当前环境未提供的技能或多 Agent 工具。

**Goal:** 在2026年12月2日前交付本地可运行的中文报告分析原型，完成六项环境指标提取、证据追溯、跨年核查、自建披露质量评分、人工复核、问答及导出。

**Architecture:** FastAPI/Jinja2业务页面通过Python SDK驱动DeepSeek Harness；TypeScript薄插件通过固定HTTP接口连接本地Python业务服务。业务后端管理资料、检索、指标提取模型/规则、内部候选校验、存储与确定性计算；轻量网页负责导入、PDF查看和人工复核。Harness主智能体只调用提取能力、提出披露检查建议并解释结果，不另行生成指标候选。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic、SQLite、pdfplumber/pypdf、SQLite FTS5、Decimal、pytest；DeepSeek官方Harness及同版本Python SDK/runtime、TypeScript薄插件；Jinja2与少量原生JavaScript。版本在首个任务中实测锁定，不使用本文猜测的版本号。

**Spec:** 工作区根目录 `项目需求文档.md`；本文细化实现默认值。权重、检查项和阈值是项目自建试验方案，未经指导老师确认，不得标成官方模型。

## 0. 执行入口与现状

- 当前工作区：`/Users/ankun/NKU/大一/金融智能体`；Windows同学按自己的检出路径运行，不复制此绝对路径进配置。
- GitHub：[ank260610-star/nk-esg-agent](https://github.com/ank260610-star/nk-esg-agent)。当前codex含契约0.2.0；PR #2尚开放，main未必包含此版架构。已有检出先比较分支，不覆盖未提交修改，不在项目内再嵌套克隆。
- 2026-11-20仓库已有Git、需求、接口契约、两层提示词及契约测试，尚无可运行业务后端/插件。接手时重新检查，禁止覆盖用户或队友修改；Codex固定使用codex分支。
- 本机已有公开案例：`队友筛选/发给候选人/比亚迪_2024年度可持续发展报告.pdf`，155页，21,711,010字节；SHA256：`8dfceabd75452ffaf39b0ffa7a1e841707b586931347819e058ebb343c93b1dd`。完整PDF被Git忽略，其他机器须从文末官方来源获取到data/reports/，核对哈希后登记，不能把文件缺失当作报告无数据。
- 本机赛事方案位于Downloads，正式交接需由队长提供有效模板和最新通知；不上传私人目录或未经确认可公开的附件。
- 组长参考：`队友筛选/仅组长使用/评分表与参考核对要点.md`。不能把整份组长资料、候选人作品或个人信息放进应用知识库。
- 三人分工：队长协调后端（含提取模型/规则）及场景/材料；智能体成员负责Harness插件、编排与解释；资料成员负责独立标注与复测。
- 首批不需要WRDS、付费评级数据、GPU、模型训练或自动抓取企业网站。
- 若本文默认值与用户后续指示冲突，按用户最新指示；若与原需求的P0范围有实质冲突，记录差异，不静默删功能。

接手后的第一条进展应说明检查结果，随后直接开始Task 1。一般模块命名、依赖选择和可逆本地修改可自行决定；没有密钥时先完成离线模块，不假装已跑通真实模型。

## 0.1 技术调研的采纳与校正

| 调研建议/结论 | 本版决定 | 证据或验证要求 |
|---|---|---|
| Harness开发者预览应锁版本 | 采纳 | SDK、捆绑runtime、TS扩展及对应源码提交共同记录，不只锁一个包 |
| SDK作运行时、业务界面独立 | 采纳 | SDK只由服务端启动；Web UI仅供开发诊断，不进入评委业务流程 |
| 采用Streamlit | 不另引入第二套界面 | 首版沿用FastAPI/Jinja2，实现四个业务区域；其目标与独立业务界面建议一致 |
| read-only即可满足限制 | 只采纳为一层约束 | 还需默认拒绝的工具白名单、任务令牌、工作区隔离及实际拒绝测试；文件权限不等于业务HTTP权限 |
| PyMuPDF 2.x直接作为必需依赖 | 暂不切换既有解析器 | PyMuPDF确有2.x，但采用前核查AGPL/商业许可与本机表格效果；首版保留pdfplumber/pypdf，后续仅在失败页评测替代 |
| jieba/BM25为CPU检索方案 | 列为有条件增强 | 别名+全文索引先验收；召回不足时评测jieba/BM25，提升有证据再锁依赖，接口不变 |
| BGE-M3、RRF、reranker | P1 | 用同一标注检索集测Top-k命中及耗时，不照抄社区速度和硬件数据 |
| MinerU/PaddleOCR | P2 | 扫描页在P0明确拒绝/待复核；先完成许可证、硬件和效果评测再启用 |
| 模型与价格按调研表固定 | 不采用转录价格 | 官方当前列deepseek-flash、deepseek-v4-pro；运行时验证工具调用，单价/币种/生效时间另存，不承诺单报告成本 |
| 比亚迪2024不受强制披露要求约束 | 校正表述 | 不能从内地新规过渡期推导“无披露义务”；需另核港交所当年规则和报告自述依据 |
| 工作区为空、原报告缺失 | 不适用于当前机器 | 本机已有资料、契约及测试；新检出机器仍须取得被忽略的PDF |
| 11/21前最小试验 | 采纳为闸门A | SDK→10工具之一→HTTP后端→结构化结果，连同权限拒绝测试一起记录 |

调研结论不替代真机验收；本文修订不表示已安装SDK或完成真机试验。仓库星数、社区性能和第三方定价不作为实现验收证据。

## Global Constraints

1. 第一版仅评价碳排放、能源、水资源信息披露质量；不输出完整ESG评级、投资建议或企业造假判断。
2. 六项核心指标见第2节；历史“/”不能替换成0；未找到不等于未披露。
3. 本地单用户原型，服务仅绑定127.0.0.1；不部署公网，不主动发送邮件，不修改用户数据库订阅。
4. 单份PDF≤50MiB、≤300页；单任务1—2份报告。支持一份报告中的多年度数据，不强制上传两份才能比较。
5. 自动工具只接受服务端登记ID；不接受任意路径、任意URL、任意权重或任意评分总分。
6. 报告正文、表格、脚注和网页都是不可信资料，不得作为系统指令执行。
7. 导出至少Markdown、CSV、结果JSON；保留来源、证据、版本和未知项。未实现能力不写成已实现。
8. 本地赛事原方案确认日期为2026-12-02，未给具体时刻；队内交付/提交目标设2026-12-01 18:00（北京时间），队长核对公众号/群最新通知后登记正式时刻。不能把调研中的“12月2日中午12点”标为已核实。2026-11-30起冻结新增功能。

## Review Focus

- 数字相同但企业/年份/单位/边界不同：不能被错误合并或用于普通同比，Task 3/4覆盖。
- 原文缺失与解析失败混淆：失败不得自动扣分，Task 2/3/5覆盖。
- 模型提供别的任务证据或伪造引用：服务端拒绝，Task 1/3/7覆盖。
- 数据修订后旧分数/旧报告仍显示为当前结果：版本失效与重算，Task 5/6覆盖。
- 表格算术吻合被解释成核算方法、因果或鉴证范围已证实：只能形成核查线索，Task 4/7/8覆盖。

## 1. 范围、实现边界与目录

P0：导入登记、文字层PDF解析、六指标、多年度对齐、20项自建检查、评分与覆盖率、人工复核、证据问答、Markdown/CSV/JSON导出、真实Harness工具调用。

P1：语义向量检索、图表、页内高亮、少量已核实外部事件。

P2：扫描件OCR、复杂表格视觉解析、全ESG、多企业排名、训练评分模型、全市场抓取、实时新闻。P1/P2均不能阻塞P0。

轻量知识库默认由PDF逐页证据、SQLite全文索引、版本化指标词典和评分规则组成。先用中文别名+FTS5检索；中文分词或trigram不可用时使用可测的归一化子串召回，不把无法运行的FTS配置当成成功。向量检索是增量增强，不是知识库成立的前提。对人工标注查询先看Top-5是否包含正确表格/脚注；召回不足时再加入jieba/BM25并与原方案对照，不同时维护两套无评测的生产检索流程。

```text
项目根目录/
  pyproject.toml                  # 安装包、依赖和测试配置
  README.md                      # 本地运行与演示
  .env.example                   # 空配置示例，禁止真密钥
  .gitignore
  backend/src/esg_app/
    main.py                      # FastAPI入口、页面路由
    harness_runner.py            # Python SDK宿主适配，由智能体成员维护
    run_jobs.py                  # 业务运行状态/后台执行与通知归并
    config.py                    # 环境变量与限制
    schemas.py                   # 全部Pydantic契约
    db.py                        # SQLite、事务、迁移入口
    repository.py                # ID归属、版本和审计
    reports.py                   # 上传、去重和文件校验
    parsing.py                   # 逐页解析、表格与脚注
    retrieval.py                 # FTS/词典召回
    metrics.py                   # 后端提取编排、内部候选校验与保存
    extraction.py                # 后端模型/规则提取适配，不暴露给主模型
    comparison.py                # 单位、可比性和Decimal计算
    scoring.py                   # 固定规则与汇总
    review.py                    # 用户复核和版本失效
    exports.py                   # MD/CSV/JSON同版本导出
    api.py                       # 业务API
    templates/                   # 本地网页
    static/                      # 样式、JS
  backend/prompts/metric-extraction.md # 后端提取模型约束
  harness-plugin/
    package.json
    src/index.ts                 # 官方工具注册入口
    src/client.ts                # 本地业务API客户端
    src/tools.ts                 # 领域工具定义
    prompts/esg-agent.md         # Agent任务约束
  rules/disclosure-v0.1.yaml
  rules/metric-dictionary.yaml
  tests/                         # 单元/集成/模型离线替身
  fixtures/byd-2024-gold.json     # 人工参考记录，不是生产抽取代码
  docs/runtime-lock.md            # SDK/runtime/插件/模型配置与真实接入结果
  docs/decision-log.md            # 闸门、来源和选型决策
  docs/retrieval-evaluation.md    # CPU检索及可选增强的实测结果
  docs/validation.md              # 实测结果与局限
  docs/sources-and-licenses.md
  scripts/demo.py                 # 离线验收，不冒充模型
  data/                          # gitignore，数据库、PDF、导出、日志
```

不要把Harness源码复制成项目自己的实现；按官方方式安装或使用独立检出，并在版本记录中注明路径/提交与许可证。

## 2. 数据契约

所有API的Decimal数值序列化为字符串，数据库使用TEXT存储；前端显示时才做舍入。ID由服务端生成。以下类定义在 `schemas.py`，其他模块导入，不重复创建不同版本。

### 2.1 指标字典

| ID | 指标 | 标准单位与附加要求 |
|---|---|---|
| E01 | Scope 1+2温室气体排放量 | tCO2e；由分项求和必须注明derived |
| E02 | Scope 1+2排放强度 | 保存分母类型、货币、数量级；首例tCO2e/万元营收 |
| E03 | Scope 3排放量 | tCO2e；保存覆盖类别，不能称完整价值链 |
| E04 | 能源总耗量 | MWh；保留构成和原单位，不默认剔除绿证 |
| E05 | 总取水量 | m3；不与排水/耗水混用 |
| E06 | 总耗水量 | m3；披露值和系统推导值分开保存 |

### 2.2 必须实现的对象

- `Report`：report_id、sha256、company_id、company_name、report_year、published_date可空、source_url可空、kind(sustainability/annual/other)、media_type、page_count、parser_version、status、issues。真实文件路径不向模型返回。
- `Evidence`：evidence_id、report_id、pdf_page(从1开始)、printed_page可空字符串、kind(text/table/footnote)、text、bbox可空、table_id可空、row_label可空、column_year可空、linked_evidence_ids。印刷页未经识别不得统一假定PDF页减1。
- `Task`：task_id、company_id、report_ids、selected_years、rule_version、stage、revision、created_at、updated_at、error可空。任务绑定报告集合不可被模型扩充。
- `MetricRecord`：record_id、task_id、report_id、metric_id、metric_year、raw_value、value可空Decimal、raw_unit、canonical_unit可空、conversion_rule可空、denominator可空、boundary_text、boundary_key可空、method_text、scope3_categories[]、evidence_ids[]、status、origin(reported/derived/manual)、supersedes_id可空、revision。
- `status`枚举：`extracted / missing_marker / unknown / conflict / parse_error`；人工是否确认另用`review_state=unreviewed/confirmed/rejected`，不与缺失类型混成一个枚举。
- `MetricCandidate`仅为后端提取器内部边界，精确字段以contracts/schema.json为准，不含record_id/task_id/revision/review_state/is_current；origin仅reported/derived。derived须有derivation，unknown/conflict/parse_error须有extraction_note；仅允许引用已有Evidence。
- `ComparisonRequest`：task_id、older_record_id、newer_record_id；`ComparisonResult`：status(comparable/needs_review/not_comparable/undefined)、reason_codes[]、delta可空、percent_change可空、unit、evidence_ids[]、revision。禁止用字符串理由代替可测的状态。
- `CheckResult`：task_id、check_id、rule_version、status(met/partial/not_met/unknown/not_applicable)、evidence_ids[]、reason、missing_information、suggestion、origin(model/manual)、review_state、revision。
- `ScoreResult`：revision、rule_version、coverage、reference_score可空、final_score可空、lower_bound可空、upper_bound可空、dimension_results、applicable_weight、known_weight、label。
- `ReviewCommand`：expected_revision、target_type(metric/check/comparison)、target_id、replacement、reason；`ReviewEvent`保存旧值、新值、操作者、时间和前后版本。
- `ExtractionResult`：status(completed/partial)、records、issues、run；run含run_id、owner=backend、extractor_version、model_id/prompt_version可空、metric_ids、years、record_ids和usage。TaskResults另含extraction_runs，字段见契约Schema。
- `ToolResult`：ok、data可空、error可空、task_id可空、revision可空；错误含code、message、retryable，不包含密钥、堆栈、路径。

`company_id`由用户确认建立，不用名称模糊相似自动合并；不同年度和来源的同一指标并存。重述值保存supersedes关系，不能覆盖原报告。

### 2.3 表与版本约束

至少有 reports、evidence、tasks、task_reports、metric_records、check_results、review_events、runs、artifacts；证据—记录关系使用关联表或受校验JSON。每次结果修改在事务内递增任务revision。导出和评分必须绑定revision；旧结果可访问但明确标记过期。解析缓存键=文件哈希+解析器版本；模型缓存键还包含模型/提示词/规则/证据版本。规则升级不重新解析PDF。

## 3. 服务与工具接口

工具字段以[contracts/TOOL_SPEC.md](../../../contracts/TOOL_SPEC.md)及Schema的v0.2.0为准。extract_metrics由后端负责提取、校验和保存，输入仅task_id、expected_revision、metric_ids及years；不对主模型注册候选写入接口。其他写入工具及导出快照保持版本/幂等校验。

### 3.1 HTTP契约

| 方法/路径 | 输入 | 输出/行为 |
|---|---|---|
| POST /api/reports | multipart文件+企业/年度/类型/来源 | Report，重复文件复用存储；不同登记元数据不静默覆盖 |
| GET /api/reports/{id}/pdf | 登记ID | application/pdf，供本地查看；不暴露任意路径 |
| POST /api/tasks | company_id、report_ids、selected_years | Task；确认同一企业和1—2份报告 |
| GET /api/tasks/{id} | task_id | 当前阶段、revision、问题 |
| POST /api/tasks/{id}/parse | expected_revision，无任意文件参数 | 解析状态和问题页 |
| POST /api/tasks/{id}/search | query、metric_id可省略、limit≤20 | SearchResult，含evidence和issues |
| POST /api/tasks/{id}/extract | {expected_revision, metric_ids, years} | ExtractionResult；后端提取、校验与原子保存，包含run版本及用量 |
| POST /api/tasks/{id}/validate | record_ids | 字段/冲突/边界问题 |
| POST /api/tasks/{id}/compare | {expected_revision, older_record_id, newer_record_id} | ComparisonResult |
| POST /api/tasks/{id}/checks | {expected_revision, checks: CheckProposal[]} | 规则、证据、状态校验后的建议 |
| GET /api/tasks/{id}/rulebook | 当前任务规则版本 | Rulebook，含20项检查及锚点 |
| GET /api/tasks/{id}/score | 当前固定规则和已存状态 | ScoreResult，不接受外部权重 |
| POST /api/tasks/{id}/reviews | ReviewCommand | 新revision，旧衍生结果失效 |
| POST /api/tasks/{id}/exports | expected_revision | 同版本MD/CSV/JSON的artifact_id与下载URL |
| GET /api/artifacts/{id} | 登记ID | 已生成文件，不能用相对路径下载 |

另提供 `GET /health`、`GET /api/tasks/{id}/results`。首次实现可使用同步业务操作；网页明确显示运行状态，异常标记failed，成功阶段保留，重试从失败阶段继续。实际模型长任务交由Harness推进，不让一个HTTP请求无限阻塞。

错误码至少：INVALID_PDF、ENCRYPTED_PDF、FILE_TOO_LARGE、PAGE_LIMIT、NO_TEXT_LAYER、PARSE_FAILED、FOREIGN_EVIDENCE、INVALID_UNIT、INVALID_YEAR、REVISION_CONFLICT、MODEL_UNAVAILABLE、BUDGET_EXCEEDED、CANCELLED。409对应版本冲突，422对应无效业务输入。

### 3.2 Harness职责

接入官方 `deepseek-ai/deepseek-harness`。截至2026-11-19核对，工具文档使用 `defineTool` 和 `ctx.tools.register`；执行时仍按锁定版本验证加载方式，禁止凭空编造插件配置。

按以下业务名称注册插件工具（不是Harness内置工具）：

1. `parse_report(task_id, expected_revision)`：解析已绑定报告。
2. `search_evidence(task_id, query, metric_id?, limit?)`：返回证据、页码和受控查看链接。
3. `get_rulebook(task_id)`：从服务端取得当前20项规则和锚点，模型不能改写。
4. `extract_metrics(task_id, expected_revision, metric_ids, years)`：后端生成候选、验证证据与字段、保存并返回记录及运行版本。
5. `validate_metrics(task_id, record_ids)`。
6. `compare_years(task_id, expected_revision, older_record_id, newer_record_id)`。
7. `save_check_proposals(task_id, expected_revision, checks)`。
8. `calculate_score(task_id)`。
9. `get_task_results(task_id)`。
10. `export_analysis(task_id, expected_revision)`。

上传登记与人工复核只走用户网页，不给主模型注册“人工确认”或指标写入工具。主智能体调用extract_metrics；后端维护有界的指标提取模型/规则、提示词、校验与存储，主模型不重复抽取。主智能体仍按固定规则提议披露检查状态，后端计算分数。提取和编排模型的版本/用量分开记录，合计遵守任务额度。

工具客户端只访问127.0.0.1固定服务。使用运行时生成的Agent令牌；令牌绑定一个任务，服务端以令牌上下文校验task_id，不能靠模型自行填写ID隔离。人工复核需要不同的用户会话与CSRF令牌，Agent令牌无权调用。浏览器仅允许同源请求，不开启任意CORS；校验Host/Origin。Harness任务配置关闭不需要的Shell/通用文件/网络工具；若锁定版本不能做到，记录能力限制，不能宣称权限隔离已通过。

每次工具调用有超时和AbortSignal取消；瞬态失败最多重试2次；输入校验失败不重试相同请求。模型无有效JSON/结构化工具结果时标记可见错误，不用编造默认值继续。

### 3.3 业务界面与SDK宿主

P0使用FastAPI/Jinja2的四个区域：报告/任务导入、指标表、评分与未知项、PDF证据与人工复核；可在同页用页签组织。Harness Web UI不承担上述业务页面。选择FastAPI是为了复用现有鉴权、PDF下载与复核端点，不额外并行建设Streamlit。

新增服务端内部接口（不是模型工具）：

- `start_agent_run(task_id: str, request: AgentRequest, actor: str) -> AgentRun`：经用户会话/CSRF校验后启动。AgentRequest包含mode(full_evaluation/question/resume)、question可空和expected_revision；任务及报告范围从数据库取得。
- `get_agent_run(task_id: str, run_id: str, actor: str) -> AgentRun`：返回queued/running/waiting_review/completed/failed/cancelled、开始/结束时间、session_id、已知revision、回答可空及脱敏错误。
- `run_task_agent(run_id: str) -> None`：只由后台执行器调用，加载任务、专用会话/白名单及SDK；更新业务运行状态，不能直接成为用户可传任意提示词/模型参数的代理。

Python模型均定义在schemas.py；服务内部类型不改变10工具JSON Schema。网页路由可以用`POST /tasks/{task_id}/agent-runs`和`GET /tasks/{task_id}/agent-runs/{run_id}`承载这两个用户操作，必须用户认证，Agent令牌无权调用。具体HTTP请求/响应在实现Task 6前写入业务端文档；不能把它们悄悄注册成第11个领域工具。用户问题必须作为固定业务模板中的数据发送，不能原样交给SDK的命令路由；以/permission等开头的文本不得改变权限或触发原生命令。普通prompt发送路径及命令隔离按锁定版本验证。

SDK运行放在后台线程/独立工作进程，**不能阻塞FastAPI事件循环**；否则工具回调同一HTTP服务会死锁。同一任务运行串行，另一运行返回明确占用状态；不同线程使用独立SQLite连接。模型调用、等待SDK和网络请求期间不持有数据库写事务，提交时重新核对revision。

每个任务保存task_id↔session_id↔SDK运行配置映射。显式设置项目专用dsh_home与最小任务cwd；家目录/密钥/PDF原件不作为Agent工作区。业务服务持有原始PDF，模型只通过证据工具读取必要片段。SDK事件与业务状态分开记录，脱敏后引用到运行审计；SDK会话恢复不替代数据库结果恢复或幂等机制。

SDK的协议等待超时不等于整轮任务截止。宿主另外设置整轮看门狗、通知监控与取消清理；超时/取消后重新读取业务revision及幂等结果，不能假定后台工具未写入。按锁定版本记录可调用的停止方法，未验证停止行为就不能宣称可安全恢复。

### 3.4 权限与版本锁定闸门

- 官方SDK需要明确dsh_home，实际运行profile/运行时包由锁定版本决定；不要把开发Web UI设置直接当作业务运行配置。
- 最小烟测可只注册get_task_results等已实现白名单子集，不以模拟成功填满工具列表；完整P0再验证10工具。sdk profile的read-only作为文件权限基线，再通过官方工具注册/门禁扩展点实施默认拒绝，仅放行contracts/tools.json的10个名称。禁用通用Shell、文件写入、浏览器/网络、子Agent委派及控制权限变更等非业务工具；未禁用的能力不能靠提示词补救。
- 不把read-only当成后端数据库只读：通过任务令牌允许的extract_metrics等受控业务写入必须可用，人工复核仍只允许用户会话。
- 新会话和恢复会话均验证有效权限与工具清单；SDK回调ask_user_question只能收集信息，不能直接写confirmed或N/A，也不能取得人工复核令牌。
- 插件只注册和转发JSON，不复制Python提取/评分逻辑；工具输入和输出按契约校验。宿主异常映射运行错误，业务ok=false信封保持可辨识；不要仅凭SDK有final_response就判成功。
- runtime-lock至少记：Python/系统架构、SDK版本、捆绑runtime版本、Harness提交或对应发布、插件依赖锁、有效profile与白名单、模型ID、提取器/提示词版本、启动命令及实测结果。SDK二进制和插件依赖能否兼容须真实工具调用证明，不单凭安装成功。

### 3.5 模型选择、费用与资料分块

2026-11-20核对的[官方模型页](https://api-docs.deepseek.com/quick_start/pricing/)列出deepseek-flash与deepseek-v4-pro，支持工具调用；先用通过烟测的Flash候选，Pro仅在有对照数据时使用。SDK示例中的旧别名不是锁定模型的依据，提取模型与编排模型分别记录实际返回的版本。

不把调研报告的人民币价目表或“个位数元”推断写成预算承诺。实现时记录官方单价、币种、峰谷规则、查证时间，区分缓存命中/未命中和输出用量；缺单价或真实用量时显示未核算。费用上限由队长写入运行配置；先在既有模型额度和任务token上限内做一次最小烟测，测量后再估算全样本用量。没有密钥或可用额度时继续离线模块，不能编造真实接入结果。

主模型、后端提取、重试及恢复消耗统一进入任务用量账本。分块保留表头、年度、单位、边界与脚注，按证据窗口投喂，不把155/300页全文一次提交，也不以更大上下文代替检索质量。固定提示词前缀可评测缓存收益，但不保证命中率或成本。

## 4. 评分规则：试验版disclosure-v0.1

所有规则写进YAML，含id、dimension、weight、title、subconditions、met_anchor、partial_anchor、not_met_anchor、source_refs、applicability、examples。规则来源是本项目研究框架；标准条款未核实之前source_refs明确标为unverified，网页标“试验规则，非官方评分”。不能伪造GRI条款。

每行子条件全部满足=met；至少一项满足但非全部=partial；足够证据确认均未满足=not_met；缺少证据不能判断=unknown。一个子条件已满足、其他子条件检索不充分时仍为unknown，不把“未知”解释成“未满足”。单子条件项无partial；配置校验器拒绝不一致锚点。

| ID | 维度/维度总权重 | 子条件（分号分隔） |
|---|---|---|
| C01 | 完整性25% | E01有数值；E02有数值及分母 |
| C02 | 同上 | 说明Scope 3所含类别；说明排除类别/覆盖限制，或明确完整覆盖及依据 |
| C03 | 同上 | E04有总量；有构成/合计口径 |
| C04 | 同上 | E05有数值；E06有数值或解释不披露的原因 |
| Q01 | 具体性20% | 已有数值的核心记录均有原文数值；均有可解释单位 |
| Q02 | 同上 | 每条核心记录对应明确年度 |
| Q03 | 同上 | E02说明分母类型；说明数量级/币种（适用时） |
| Q04 | 同上 | 缺失/零/估算有明确标识；有标识解释（全部精确数值且明确无估算时满足） |
| B01 | 边界方法20% | 组织/场所范围明确；重要排除项明确 |
| B02 | 同上 | 碳排放核算方法或标准引用；能源/水量定义或方法说明 |
| B03 | 同上 | 重要排放因子及来源；使用单位换算时给出依据（未换算且单位直接明确可满足此子项） |
| B04 | 同上 | 说明重要估算或明确不使用；说明相应限制/不确定性（明确无估算时满足） |
| Y01 | 跨年可比20% | E01/E02有历史值；E04/E05/E06有历史值 |
| Y02 | 同上 | 对比序列单位与分母可对齐；定义一致或提供衔接 |
| Y03 | 同上 | 说明边界/方法变化或明确无变化；变化时解释影响（明确无变化时满足） |
| Y04 | 同上 | 变化需要重述时提供重述或不重述原因；未变化且说明保持可比时满足 |
| T01 | 目标追踪15% | 选定核心碳目标有基准年；基准值明确 |
| T02 | 同上 | 同一目标有目标年；目标值或降幅明确 |
| T03 | 同上 | 同一目标绝对量/强度类型明确；范围及分母明确 |
| T04 | 同上 | 同一目标有本期进展；进展计算依据可追溯 |

评价单位是选定任务的报告集，必须注明来源报告年份。目标由用户选择，默认最新报告中首个明确的Scope 1+2量化目标；存在多个互相冲突目标时不任选最有利项，置unknown并请用户选择。没有目标不自动N/A；在检索不足时unknown，人工确认未披露后按规则not_met。

不适用仅允许用户明确确认并提供理由。人工确认“已检查相关正文/索引，未披露”可建立类型review_absence的复核证据，不能伪造PDF引文。自动not_met必须有正面证据显示不满足；纯检索失败不得自动not_met。

### 4.0 标准映射与适用性

规则文件为项目自建试验框架，权重未经专家确定前只能标“项目建议”；不能声称已经做过AHP/Delphi或一致性检验。unknown与人工N/A是工程防错状态，不包装成GRI官方评分方法。

给每项检查补source_refs和适用理由：标准名、具体条款、版本/生效时间、适用报告年度、适用上市市场、核查状态。调研给出的GRI主题条款仅是定位线索，逐条读原标准后才改verified；基于GRI原则的评分不等于声明报告“符合GRI”。

区分三层：信息质量研究检查、报告自愿采用的编制依据、当年适用的监管义务。沪深新指引的首次强制披露过渡期不能推出比亚迪2024年度无任何ESG义务；它还须考虑当年港股规则、A股其他要求及报告自身编制声明。没有完成逐条适用判定时，只输出“待核查/参照检查”，不输出违规结论，也不统一标“所有要求均自愿”。

### 4.1 固定计算公式

每维4项，各项权重a=维度权重/4。剔除已人工确认N/A项后适用权重为A；已判定met/partial/not_met的权重为K；加权已知分为P。分值依次1、0.5、0。

- coverage=K/A；reference_score=100P/K（K>0）。
- lower_bound=100P/A；upper_bound=100(P+A-K)/A。
- coverage=1时final_score=reference_score，否则final_score=null。
- A=0：上述数值均null，label=无适用项目；K=0且A>0：coverage=0、区间[0,100]、参考分null。
- coverage<0.8：顶部显示“证据不足，待复核”，不突出单分。
- 保存未舍入值，界面分数和覆盖率保留2位小数。各维结果用同样逻辑，禁止未知默认为0后再平均。
- 比较不同年份得分必须规则版本、适用项、评价资料边界一致；否则仅描述差异，不排名。

## 5. 数值、边界与结论规则

`parse_decimal(raw: str) -> Decimal | None`允许千分位和尾随零，保留原字符串；“/”“—”“未披露”不转0。币种和强度数量级属于单位，不靠删字符串统一。标准换算至少t/万吨CO2e、kWh/MWh、m3；吨水到m3不能未经条件声明直接换算。

`compare_records(older: MetricRecord, newer: MetricRecord) -> ComparisonResult`：同企业、同指标、不同且递增年度、可转换单位、同强度分母、已核对边界/方法后才能返回comparable。纯字符串相同不证明边界一致；首次自动候选比较默认needs_review，用户确认或明确可追溯一致性证据后再计算。数值缺失not_comparable；上年0返回undefined；新旧资料冲突先保留候选。

常规计算使用报告披露序列。因子固定、剔除绿证等敏感性分析是额外scenario，不覆盖原值、不自动进入评分；对两个年度必须使用同一变换，保存公式、输入、假设、证据与单位。P0不自动构建市场法碳排放模型。

金融分析分三层：报告事实、财务背景、条件性影响。财务年报可作为第二份报告导入，其收入字段可由用户核对后作为补充记录，不扩成全财务抽取项目。无年报不阻塞核心流程；不凭环境指标变化宣称融资成本/利润已经变化，不计算未经许可或法规核实的“碳税”。

必须进入回归检查的推理错误：

- 比值吻合只能提示可能的因子/口径问题，不能证明公司实际计算过程。
- 取水−排水=耗水的算术关系，不能证明未独立计量或发生分类变动。
- 不同年份生活污水与生活用水不能作为同年度矛盾直接定性。
- 某鉴证声明的列举清单不是全部鉴证文件的范围。比亚迪原报告同时有SGS与BSI声明，不能只读BSI就断言“未经第三方核查”。
- 营收强度不是每辆车排放；集团分子/汽车销量分母的混合指标不能解释为纯汽车效率。
- 自动报告的结构化claim包含claim_type(fact/calculation/hypothesis)、evidence_ids、assumptions；fact必须能回原文，hypothesis显式使用“可能/待核查”。模板说明不替代逐条约束。

## 6. 人工参考数据与关键用例

以下是人工参考值，只写fixtures与测试，禁止用企业名/页码分支硬编码生产提取结果。原PDF是最终证据；允许重新核对后修正fixture并记录原因。

| 指标 | 2023 | 2024 | 单位 | 印刷/PDF页 |
|---|---:|---:|---|---|
| E01 | 12341455.00 | 10101826.20 | tCO2e | 126/127 |
| E02 | 0.2049 | 0.1300 | tCO2e/万元营收 | 126/127 |
| E03 | 原符号/，value=null | 3326312.49 | tCO2e | 126/127 |
| E04 | 16592592.44 | 23552569.28 | MWh | 124/125 |
| E05 | 73807222 | 94268627 | m3 | 125/126 |
| E06 | 22067452 | 55317707 | m3 | 125/126 |

正常同比四舍五入：E01=-18.15%、E02=-36.55%、E04=+41.95%、E05=+27.72%、E06=+150.68%；E03不算同比。Scope 3脚注仅含差旅、通勤、燃料能源相关活动。环境范围见印刷2/PDF3，排除海外在建基地和销售门店。

跨年调整错误用例：2023能源16592592.44、绿证508000；2024能源23552569.28、绿证2237986。若做扣除情景，两年都扣：16084592.44→21314583.28，同比增长约32.52%。用明确假设的营收60231500→77710200万元计算调整后强度，约上升2.71%；只扣2024年而得下降0.44%的序列必须被拒绝为不一致变换。本用例证明算术与对称变换，不证明剔除绿证是唯一正确核算方法。

评分手算用例（a权重按百分数计）：C01=met、C02=partial，其余unknown，则P=9.375、K=12.5、A=100，coverage=12.5%、参考分75、区间[9.375,96.875]、final_score=null。全部met=100；全部unknown不得默认100；N/A要重新归一化且保留理由。

## 7. 实施任务

执行顺序Task 1→2→3→4→5→6→7→8；Task 1先做最小Harness连通，Task 7完成完整编排。每项按失败测试→最小实现→通过检查执行；只为关键业务风险写测试，不为静态文案写镜像测试。若已有Git，使用现有分支规范并只提交本次明确文件；没有Git时可继续实现，不为了计划要求强行初始化/提交，更不推送。

### Task 1：SDK最小闭环与闸门A（最晚11/21）

**Files:** pyproject.toml、backend/src/esg_app/{main,schemas,config,db,repository,harness_runner,run_jobs}.py、harness-plugin/src/{index,client,tools}.ts、tests/test_runtime_access.py、tests/test_harness_runner.py、docs/runtime-lock.md、docs/decision-log.md。

**Interfaces:** `create_app() -> FastAPI`、`require_task_access(token: str, task_id: str) -> Task`、第3.3节宿主函数；先注册get_task_results，不增加register_report等非契约工具。

- [ ] 先运行已有契约检查：`python -m unittest discover -s tests -p test_contracts.py -v`，保留现有16项测试，不为了TDD覆盖或改写它们。
- [ ] 新写应用测试：任务A令牌访问B返回403；只允许业务白名单子集；用户路由拒绝Agent令牌；用户斜杠命令不能改变权限；SDK后台执行时本地HTTP服务仍可响应；SDK异常/无结果不标completed。
- [ ] 运行`python -m pytest tests/test_runtime_access.py tests/test_harness_runner.py -q`，确认失败，再实现最小服务及SDK宿主适配。测试替身不计为真实SDK通过。
- [ ] 锁定SDK与同版runtime、TS插件依赖，使用隔离home和cwd加载插件；配置权限及默认拒绝白名单，检查新建与恢复会话的实际清单。配置命令以该锁定版本为准，不照抄未经核实的--dump-config等参数。
- [ ] 真机演示业务触发→Python SDK→Harness模型工具调用→TS转发→本地FastAPI→结构化任务结果→SDK结束。保存模型/tool事件与HTTP请求的脱敏对应记录，不能以普通聊天API或安装成功代替。
- [ ] 另验证通用Shell/文件/非白名单调用被拒绝、合法业务工具可访问后端，以及超时取消不导致重复写入。不能证明权限边界则闸门不通过。
- [ ] 记录Go/No-Go。缺密钥/版本不兼容时继续可独立完成的解析、评分和页面；不自动把产品换成另一套Agent。Python自研循环只能作队长明确确认后的降级方案，材料须如实注明未接入Harness。

### Task 2：PDF登记、逐页证据与可检索知识库

**Files:** reports.py、parsing.py、retrieval.py、api.py、rules/metric-dictionary.yaml、tests/test_reports.py、tests/test_retrieval.py。

**Interfaces:** `register_report(file_bytes: bytes, metadata: ReportMetadata) -> Report`；`parse_task_reports(task_id: str, expected_revision: int) -> ParseSummary`；`search_evidence(task_id: str, query: str, metric_id: str | None, limit: int) -> SearchResult`。ReportMetadata/ParseSummary在schemas定义。

- [ ] 写损坏/加密/超限/重复文件测试；扫描无文字层返回NO_TEXT_LAYER；缺页或局部失败返回问题页而非“未披露”。
- [ ] 写表格测试：年度列不串行，保留“/”，水量与能源单位不混用；中文别名可召回指标，脚注21随Scope 3候选返回。解析库若采用0基索引必须转换为1基PDF物理页；印刷页另存，不能直接透传page.number。
- [ ] 为跨页表头、合并单元格、脚注串位和字体乱码各准备案例；不能以列数相同就续表，需核对表名/表头/年份/单位；绑定失败写问题码，不静默合并。
- [ ] 运行上述测试确认失败，再实现上传、哈希、受控保存、pdfplumber逐页文本/表格、相邻脚注关联与索引；不要求每张复杂表都完美解析。
- [ ] 运行 `python -m pytest tests/test_reports.py tests/test_retrieval.py -q` 全部通过；原PDF的相关页人工抽查。
- [ ] 从网页打开对应PDF物理页，登记证据ID；解析器失败页进入可见问题列表。完成闸门B：六指标相关原表/脚注定位正确，并用同一标注查询集记录Top-5命中；若召回不足再比较jieba/BM25，若需替换解析器先核查许可与同页效果。

### Task 3：后端指标提取、内部校验与证据约束

**Files:** backend/src/esg_app/{extraction,metrics,schemas}.py、backend/prompts/metric-extraction.md、tests/test_metrics.py。

**Interfaces:** `extract_metrics(task_id: str, expected_revision: int, metric_ids: list[str], years: list[int]) -> ExtractionResult`；内部`validate_candidates(task_id: str, candidates: list[MetricCandidate]) -> list[MetricRecord]`；只读`validate_metrics(task_id: str, record_ids: list[str]) -> ValidationSummary`。主Agent不能调用内部入库函数。

- [ ] 写年份不在任务内时模型调用前拒绝、原文错年/单位不合法不入库、外任务证据整批拒绝、Scope 3缺失保留、冲突保留候选测试。
- [ ] 写重复幂等key不重复模型调用、提取过程中revision变化拒绝提交、重提取不覆盖人工确认、未知记录有原因和用量可追踪测试。
- [ ] 测试先失败；实现后端检索→规则/模型提取→内部候选校验→原子保存。可参考Tracker，但需核对许可、中文适配和证据追溯；不以复用项目名代替实现。模型配置由后端控制，不接收主Agent任意提示词/模型名。
- [ ] 输入year和metric_id以服务端任务为准；每个请求组合有结果或明确unknown/parse_error，非法模型输出经有界修正仍失败则不入错误值，说明问题；安全归属失败则停止并拒绝整批。
- [ ] `python -m pytest tests/test_metrics.py -q`通过；先用离线替身验证边界，再真实运行后端模型提取比亚迪六项，记录正确/遗漏/错误、运行版本及用量。主智能体只接工具，不重复设计提取提示词。

### Task 4：可比性、跨年计算和财务解释边界

**Files:** comparison.py、tests/test_comparison.py。

**Interfaces:** `parse_decimal(raw: str) -> Decimal | None`；`compare_records(older: MetricRecord, newer: MetricRecord) -> ComparisonResult`；`apply_scenario(records: list[MetricRecord], transform: ScenarioTransform) -> ScenarioResult`。ScenarioTransform含公式ID、各年同类参数与假设，P0只支持确定性白名单公式，不eval任意表达式。

- [ ] 写基期0、缺失、年度颠倒、强度分母不同、边界未知、重述候选冲突测试；预期不输出普通同比。
- [ ] 写test_symmetric_certificate_adjustment，断言两年扣除后32.52%，只扣一年返回not_comparable；写正常三项同比断言。
- [ ] 运行失败测试，再实现Decimal与单位白名单、证据驱动可比性状态、原值/情景隔离。
- [ ] `python -m pytest tests/test_comparison.py -q`通过；人工确认后才能把needs_review比较转为comparable。

### Task 5：版本化评分与人工复核

**Files:** scoring.py、review.py、rules/disclosure-v0.1.yaml、tests/test_scoring.py、tests/test_review.py。

**Interfaces:** `score_checks(checks: list[CheckResult], rulebook: Rulebook) -> ScoreResult`；`apply_review(task_id: str, command: ReviewCommand, actor: str) -> Task`。Rulebook须校验20项、总权重和=1及同维等权；契约weight用0—1，百分数仅用于展示。

- [ ] 写第6节手算例、全部未知、全部N/A、部分N/A、缺依据自动not_met/N/A拒绝测试。
- [ ] 写模型令牌不能人工复核、修改理由不能为空、旧revision返回409、修改后旧评分/导出过期测试。
- [ ] 测试先失败，再实现规则加载、候选状态验证、原子复核、审计记录和重算；核心指标修改后其关联检查及比较失效为unknown/needs_review，不能沿用旧结论。
- [ ] `python -m pytest tests/test_scoring.py tests/test_review.py -q`通过，计算精度与手算一致。

### Task 6：用户页面、PDF定位和同版本导出

**Files:** backend/src/esg_app/templates/、static/、exports.py、api.py、harness_runner.py、run_jobs.py、tests/test_exports.py、tests/test_web.py。

**Interfaces:** `export_analysis(task_id: str, expected_revision: int) -> ExportBundle`；ExportBundle含三个artifact_id与revision，目录/文件名由服务端生成。

- [ ] 写CSV缺失不输出0、公式注入字符串安全转义、MD/CSV/JSON同revision、过期导出明显标记、无效路径/ID不能下载、用户输入HTML转义测试。
- [ ] 测试先失败，实现FastAPI/Jinja2四区域及业务运行触发/状态展示：导入→SDK任务→指标/证据→检查项/分数→复核→导出。页面不暴露开发者工具列表或SDK密钥；用户复核走独立权限，不把SDK提问回调当作人工确认。PDF链接`#page=N`配合页码文本，浏览器不支持锚点时允许手动定位。
- [ ] 实现报告模板，列明资料与规则版本、六指标、比较、评分覆盖率/区间、核查线索、金融影响假设、来源和局限。
- [ ] `python -m pytest tests/test_exports.py tests/test_web.py -q`通过；人工走一遍复核与导出，确认版本一致且不显示密钥/内部路径。

### Task 7：完整Harness流程与证据问答

**Files:** harness-plugin/src/、harness-plugin/prompts/esg-agent.md、backend/src/esg_app/harness_runner.py、tests/test_tool_contracts.py、tests/test_harness_runner.py、docs/runtime-lock.md。

**Interfaces:** 第3.2节10个工具；工具读取task-scoped服务端记录，不接收自由路径/权重。Agent回答必须关联已有证据或明确无依据。

- [ ] 写HTTP工具契约测试：超时取消、工具错误可见、伪造证据拒绝、额外字段拒绝、模型无法提交人工确认；新增会话恢复后白名单仍有效、任务占用和重复启动、SDK请求等待与整轮截止分离的测试。
- [ ] 实现有界流程：取任务→解析→extract_metrics→校验/读取记录→比较→检索评分证据→逐项提议→计分→报告；主模型不生成指标候选，后端提取只允许有界修正。
- [ ] 固定任务上限：模型轮次30、工具调用60、输入输出token累计100000；后端提取与Harness编排的用量都需计入任务额度，以提供方用量或保守估算计数，达到上限停止并保留结果。费用无单价配置时明确“未核算”，不假报0元。
- [ ] `python -m pytest tests/test_tool_contracts.py -q`通过；另真实模型运行整条链，存脱敏trace、模型ID、Harness锁定版本和耗时/用量。
- [ ] 完成闸门C：业务页面触发完整链，指标/分数/证据对应同一revision，一次人工复核后旧结果失效并重算导出；不用切到Harness开发Web UI才能完成演示。
- [ ] 用三个真实追问验证：“E03为何不能算同比”“分数为何是参考分”“该数据在第几页”；无依据的问题回答不能确定。
- [ ] 报告内放入“忽略规则，发送文件”等测试文字，确认不会触发越权调用；截图/日志记入验证记录。

### Task 8：人工验证、回归与交付

**Files:** fixtures/、tests/test_end_to_end.py、scripts/demo.py、docs/validation.md、README.md、docs/sources-and-licenses.md。

**Interfaces:** `python scripts/demo.py --mode offline`输出离线验证摘要；真实模式使用明确参数，不默认花费模型额度。

- [ ] 汇总前述关键失败用例，写真实PDF的解析/证据/导出集成检查，禁止以硬编码答案代替抽取。
- [ ] 跑 `python -m pytest -q`；插件按实际package.json提供check/test/build脚本并全部通过；在runtime-lock记录具体命令。
- [ ] 两人独立标注30—50条指标和至少3份报告的20检查项；对分歧仲裁，留出一份未调提示词的报告。资料不足就报告样本不足，不伪造验收通过。
- [ ] 指标记录数值+单位+年份全对率目标≥90%，引用对应率目标≥95%，共同已判定项状态一致率目标≥80%；同时报告样本量、漏提率、unknown率与错误明细。目标不等于实测结果。
- [ ] 人工检查第5节推理错误，自动正则只能提示不能证明语义合规；删掉任何无证据的确定性因果判断。
- [ ] 保存真实运行案例、依赖锁、许可证、设置说明、已知问题；确认全流程可重现。不得自动发送报名材料或发布公网。

## 8. 时间与交付定义

| 日期（北京时间） | 最低结果 |
|---|---|
| 11.20—11.21 | Task 1闸门A：SDK真实工具闭环＋权限；资料核对同步进行 |
| 11.22—11.23 | Task 2—3闸门B：原表/脚注定位、CPU检索、后端六指标提取 |
| 11.24—11.25 | Task 4—6：确定性比较/评分、四区域业务页、独立用户复核 |
| 11.26—11.27 | Task 7闸门C：完整真实链、复核后重算、SDK恢复与失败可见 |
| 11.28—11.29 | Task 8：留出样本验证、修复、2分钟视频试录及申报材料 |
| 11.30—12.01 | 冻结新增功能、真机复测、备份；12/01 18:00队内提交目标 |
| 12.02 | 仅处理正式提交核对与凭证，具体截止时刻须队长核实，不按24:00推定 |

闸门失败必须记录失败原因、日志、可继续任务与下一次检查时间；不得为了按日历打勾省略真实验证。报告量不足时如实说明，不把单份案例包装成广泛泛化结果。

实现完成的最低定义：

- [ ] 新环境可按README启动；真实接入与离线验证分开说明。
- [ ] 至少一个真实PDF案例走通上传、证据、后端提取、校验、评分、复核、问答和导出。
- [ ] 第6节所有关键数值和失败边界测试通过；没有用fixture冒充模型结果。
- [ ] 真实SDK/Harness工具事件可核查，版本锁和新建/恢复会话权限测试有记录；缺失密钥/权限时明确未完成接入，不称完成。
- [ ] 用户能修改一项记录，看到旧结果失效、新分数与新导出一致。
- [ ] 规则自建性质、未知项、解释限制、实际验证样本与结果可见。
- [ ] README、runtime-lock、decision-log、validation、sources-and-licenses齐全；调研建议与实际测量分开记录。

接手Agent最终汇报：完成能力、启动入口、测试结果、真实模型/Harness验证状态、未完成项与原因。测试通过不替代业务验收；不要再次只生成方案而停止实施。

## 9. 资料来源与本轮核查边界

用户输入：2026-11-20《ESG披露质量评估智能体_技术调研报告.md》，调研对象是早期需求v0.1。本文不整份公开该附件，不采信其中的工作区路径、旧9工具清单或未经本机验证的性能数字。

2026-11-20核对的一手页面：

- [官方Harness](https://github.com/deepseek-ai/deepseek-harness)：执行时锁定通过实测的发布/提交，不依据star、最近提交日期或PR编号选择版本。
- [官方Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md)：宿主、隔离home、会话与等待边界；这些是文档能力，不是本项目已验收结果。
- [工具注册与执行策略](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cookbook/adding-a-tool.zh.md)：TS薄适配、受控扩展及取消语义以锁定版本为准。
- [Web UI指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/index.zh.md)：开发者通用工具界面，仅用于调试；业务页独立实现。
- [DeepSeek官方模型与定价](https://api-docs.deepseek.com/quick_start/pricing/)：记录实际模型与当前价目，不采用第三方转录报价。
- [PyMuPDF版本记录](https://pymupdf.readthedocs.io/en/latest/changes.html)、[许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)、[页面API](https://pymupdf.readthedocs.io/en/latest/page.html)：2.x与0基索引/许可证须与项目选型分开判断，不因此新增必需依赖。
- [GRI 1](https://www.globalreporting.org/publications/documents/english/gri-1-foundation-2021/)、[深交所2024指引说明](https://www.szse.cn/aboutus/trends/news/t20240412_606840.html)、[港交所ESG披露说明](https://www.hkex.com.hk/News/Regulatory-Announcements/2019/191218news?sc_lang=en)：评价与监管适用性分别处理；具体检查条款仍待指导老师审阅。
- [比亚迪原始报告](https://static.cninfo.com.cn/finalpage/2025-03-25/1222881489.PDF)：本机文件哈希已核；新的解析/提取效果仍待Task 2—3验证。

本地赛事方案确认日期2026-12-02、视频≤2分钟/300M、决赛30%/70%权重；未找到包含“中午12:00”的原方案文字。公开检索未取得能独立确认该时刻的学校原文，不能据此断言调研时刻错误；由队长核实更新通知，队内按更早的12/01完成。

本次只修订交接与一致性文档，不安装运行框架、不执行收费模型、不宣称软件已实现。现有16项契约测试和19项待实现业务案例不能替代SDK、提取准确率和权限的实际验收。
