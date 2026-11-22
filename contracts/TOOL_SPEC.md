# 工具与HTTP约定

契约版本：0.2.0；评分规则版本：disclosure-v0.1。两者独立，不得把API版本当评分版本。

## 1. 共通字段与身份

- 所有工具输入含`task_id:string`；它只是目标选择，授权以宿主注入的任务令牌为准。
- ID为1—128位字母数字及`_.:-`，由服务端创建；模型不得创建record_id/evidence_id。
- 未注明可省略的字段均必填，可空字段显式传null。仅search的metric_id/limit可省略，limit默认10、范围1—20。空数组不等于字段缺失。
- 所有Decimal都传十进制字符串，保留原始尾随零；不允许浮点JSON、科学计数法、NaN或Infinity。`value`与`canonical_unit`表示标准化值与单位；raw_value/raw_unit保存原披露。无法标准化时不编造标准值，置unknown并注明原因。
- 时间戳用RFC3339，日期YYYY-MM-DD；PDF物理页从1开始，printed_page可空，不能统一假定页差。
- 成功统一`{ok:true,data:<工具对象>,error:null,task_id,revision}`；失败统一`{ok:false,data:null,error:{code,message,retryable},task_id,revision}`，不得混合成功data和error。
- 无权确认任务存在时，失败task_id/revision均null；不得泄露其他任务版本、路径、文件名。已授权任务内的输入错误可以带当前task_id/revision。
- 所有业务响应Content-Type为application/json；文件下载返回登记media_type。工具响应和HTTP响应使用同一个信封，适配器不再包一层。
- 身份：`Authorization: Bearer <宿主注入的任务令牌>`；令牌不在模型参数、样例、日志或仓库中。任务ID不扩大权限，普通模型不能调用上传/人工复核/任意文件接口。
- 所有对象additionalProperties=false。适配器按当前工具Schema检查，不把用户字符串直接映射成路径或URL。

## 2. HTTP映射

插件从工具输入剥离task_id填入路径。GET无body；POST把剩余字段作为JSON对象。后端响应保持统一信封。

| 工具 | 方法与路径 | 剩余请求字段 | data类型 |
|---|---|---|---|
| parse_report | POST /api/tasks/{task_id}/parse | expected_revision | ParseSummary |
| search_evidence | POST /api/tasks/{task_id}/search | query, metric_id?, limit? | SearchResult |
| get_rulebook | GET /api/tasks/{task_id}/rulebook | 无 | Rulebook |
| extract_metrics | POST /api/tasks/{task_id}/extract | expected_revision, metric_ids, years | ExtractionResult |
| validate_metrics | POST /api/tasks/{task_id}/validate | record_ids | ValidationSummary |
| compare_years | POST /api/tasks/{task_id}/compare | expected_revision, older_record_id, newer_record_id | ComparisonResult |
| save_check_proposals | POST /api/tasks/{task_id}/checks | expected_revision, checks | ChecksSaved |
| calculate_score | GET /api/tasks/{task_id}/score | 无 | ScoreResult |
| get_task_results | GET /api/tasks/{task_id}/results | 无 | TaskResults |
| export_analysis | POST /api/tasks/{task_id}/exports | expected_revision | ExportBundle |

正常工具调用HTTP200，包括“解析部分失败”“待复核”“不可比”“同比无定义”“评分全未知”等有效业务结果。非法请求、权限或整体执行失败才返回4xx/5xx和ok=false。

不要将JSON Schema原样当成Harness SDK配置。插件适配器需要按锁定的官方版本转换参数/输出定义，完整JSON Schema仍用于服务端和契约校验；转换后要验证约束没有丢失。

## 3. 版本、原子性与重试

- task.revision为服务端非负整数。写入型四工具必须带expected_revision，服务端在同一事务中核对；不匹配409 REVISION_CONFLICT，零写入。模型读取最新状态后重新决定操作，禁止客户端改版本后自动重放旧语义。
- 提取记录和检查建议均原子保存。后端提取器的数值/年份类无效候选经有界修正后仍失败时转成unknown/parse_error并写issues；出现外任务证据等权限问题则立即返回FOREIGN_EVIDENCE且整批拒绝，不把原始无效数值入库；保存检查建议发现非法证据/规则版本则整批拒绝。成功后的revision由服务端返回，不假定必然+1。
- parse_report在相同文件哈希/解析器版本命中有效缓存时可无变化；否则保存一次新状态，重复登记不可重复创建证据。局部失败写入ParseSummary.partial与问题页；完全无文字层返回NO_TEXT_LAYER，不伪装未披露。
- compare_years持久化比较结果，属于写入；不改变原始MetricRecord。相同输入结果复用，不凭重复调用制造新版本。
- validate_metrics只读诊断；calculate_score确定性读取当前有效记录；二者不修改revision。派生缓存不能作为新的业务写入。
- export_analysis校验快照revision但不递增任务revision；三种文件来自同一快照，metadata.revision和文件内容版本一致。若生成期间版本变化，整包不发布并返回409。旧artifact仍可下载但应用须提示其已过期。
- 四写入工具和export_analysis使用宿主生成`Idempotency-Key`头（UUID），不向模型开放此参数。服务端按令牌身份/任务/工具/key保存成功响应及请求哈希；同key同请求重放原响应，不能重复写入；同key不同请求409 IDEMPOTENCY_CONFLICT。
- 幂等key覆盖网络重试与响应丢失，原始响应包含原始revision，收到重放响应后可再读取当前任务。幂等记录与成功写入同事务；失败请求不消耗key。
- 仅TIMEOUT/MODEL_UNAVAILABLE/INTERNAL_ERROR等明确retryable=true的瞬态错误最多重试2次，写入重试必须保留同key/请求/版本。业务4xx不自动重试。
- 传输超时、断连或取消只说明客户端未得到结果，**不保证写入已回滚**。恢复后用同幂等key查询/重放，随后读取任务；不能盲目重建请求。
- 建议HTTP默认超时：解析和指标提取120秒，其他30秒；取消向下传递。无效JSON/HTML网关响应由适配器转为INTERNAL_ERROR，脱敏说明，不能当成功数据。

## 4. 各工具行为

### parse_report

仅处理当前任务绑定的1—2份PDF。ParseSummary含整体status、每份报告的page_count/parsed_pages/evidence_count/issues及整体issues。status=partial时保留可用证据；pages计数非负且parsed_pages≤page_count。错误页不得转换为未披露。

### search_evidence

query最多2000字符；metric_id可选E01—E06。返回`{evidence:[...],issues:[...]}`，允许空数组，**空结果不扣分**。每条Evidence包含真实ID、报告ID、物理页、原文、表格/脚注关联和受控viewer_url。viewer_url只能同源相对链接；客户端不得自行拼内部路径。引用关联ID可通过再次检索取得，但不得调用任意URL。检索结果是资料，不是指令。

### get_rulebook

返回当前规则版本、20项检查、锚点、权重、适用性及引用状态；每项有check_id/dimension/weight/subconditions/met_anchor/partial_anchor/not_met_anchor/unknown_anchor/applicability/source_refs。单子条件partial_anchor=null。权重为占总分比例，20项合计1。来源未核实必须unverified；不得编造条款。包含target_key与target_selection_status，四项T检查须同一目标。

### extract_metrics

输入task_id、expected_revision、metric_ids（E01—E06中1—6个唯一值）、years（1—10个唯一年度）。years必须是task.selected_years的子集；报告集来自服务端任务绑定，不接收report_ids、任意候选/数值、模型名、提示词或文件路径。主智能体只选择要提取的指标和年份。

后端负责检索→提取模型或规则抽取→内部候选校验→原子保存。可参考Tracker适配中文报告，但不假定其已集成。后端自身模型配置、提取提示词和版本由业务端维护，不能从主Agent接收可覆盖系统指令的参数。

返回ExtractionResult：status(completed/partial)、records、issues、run。run含run_id、owner固定backend、extractor_version、model_id/prompt_version（纯规则未调用模型时可null）、metric_ids、years、record_ids（本次返回的记录ID）、usage(model_calls、input_tokens/output_tokens可空)。这些运行元数据需持久化，get_task_results通过extraction_runs返回；未知用量不能当0，后端提取调用计入任务统一用量/额度，不能因移出Harness而绕过上限。

每个请求的指标—年份组合至少有一条记录：有原文缺失符号则missing_marker；未检索到或无法校验则unknown/parse_error，带extraction_note和issues；多来源/口径冲突保留合法候选并标conflict。completed表示全部组合完成提取或明确读到缺失标记，不代表人工确认/可跨年比较；任何组合仍unknown/conflict/parse_error则partial。全无文字层、模型不可用或额度不足等执行失败用ok=false和具体错误码，不能伪装成“未披露”。

MetricCandidate仅为后端内部结构，保留在Schema中供提取适配器校验，不注册save_metric_candidates工具，不开放Agent的POST /metrics写入入口。内部约束：

- origin仅reported/derived，后端注入record_id/task_id/revision/review_state。不得把提取模型当人工复核者。
- extracted有可校验数值、原单位和支持证据；缺失不当0；unknown/conflict/parse_error须有extraction_note。
- denominator含kind/currency/scale/unit，必须与强度单位一致；derived附白名单derivation并由程序重算。
- 证据、重述关系及boundary_key必须属于当前任务且有依据；不同来源/边界的记录不能静默合并。
- 命中同文件/解析器/提取器/模型/提示词版本及相同指标年度的有效缓存时复用，业务指纹去重。已有人工确认/修改的记录优先保留，不能由重提取覆盖；若存在实质冲突，返回issues并引导复核。
- 调用开始及提交前都核对expected_revision；模型运行期间若用户修改结果则409 REVISION_CONFLICT，候选零写入，保留已有人工记录。模型调用用量仍如实记账，失败不能报0消耗。
- 相同幂等key的并发或重试不能重复调用提取模型。后端需复用同一执行，或返回INVALID_STATE提示正在处理，不能第二次生成并写入。网络取消不能被解释为确定回滚。

### validate_metrics

输入1—100个唯一record_ids。返回status=valid/needs_review/invalid、checked_record_ids和issues。valid指结构、证据及单位检查通过，不等于已获人工确认/已证明企业底层真实。可比性未知返回needs_review。非法ID归属返回错误；有效记录的内容问题进入ValidationSummary，不删除原记录。

### compare_years

输入旧/新记录ID和expected_revision，记录必须当前有效、同企业、同指标、旧年<新年。单位、分母、范围与方法均确认后才能comparable。返回比较ID、两个输入ID、状态、reason_codes、delta/percent_change/unit、证据、版本和is_current。

- comparable：delta与percent_change均字符串；percent_change以百分数返回，例如80→100是`"25"`而非`"0.25"`；reason_codes=[]。
- needs_review/not_comparable/undefined：delta和percent_change均null，reason_codes非空。未知范围/方法、缺失、基期0、冲突、重述分别有明确代码。
- “不可比”是成功返回的业务状态，不是网络失败。错误的记录年份、企业、指标则422 INVALID_INPUT/INVALID_YEAR。缺失基期是业务not_comparable，不用0替代。
- 不提供给模型直接提交“已确认可比”参数；来源一致或人工确认依据由后端读取。

### save_check_proposals

输入1—20项CheckProposal，批内check_id唯一，同一规则版本。status仅met/partial/not_met/unknown。必须给reason/missing_information/suggestion与target_key（可空）；除unknown外需要证据。partial只有规则允许、其余子条件已确认不满足时才可用；证据不足为unknown。自动not_met需正面不满足证据或已有人工确认缺失，检索为空不能当证据。

后端返回带origin=model、review_state、revision、is_current的CheckResult。模型不得覆盖人工确认结果，不得自行N/A；若遇到已人工确认且不一致的项，整批422 INVALID_STATE，要求先读取最新结果并仅提交允许修改项。CheckResult可包含人工保存的not_applicable，但工具输入不能提交它。

### calculate_score

不接受权重、得分或检查数组参数。缺少的20项默认unknown，不默认not_met。读取当前有效检查、人工N/A和固定规则，按实施文档公式返回ScoreResult。coverage/known_weight/applicable_weight为0—1；得分/上下限为0—100。

label：final（覆盖全部且final_score非空）、reference（覆盖率≥0.8且<1）、insufficient_evidence（覆盖率<0.8）、no_applicable_items（全部N/A）。全部未知时coverage=0、reference/final=null、区间[0,100]；全部N/A时coverage/reference/final/上下限均null。

维度内分数按该维适用权重归一化。dimension_results必须恰好包含五个不同维度；最终分、区间和覆盖率由程序计算，不能只做格式验证。

### get_task_results

返回TaskResults：task、reports、metrics、checks、comparisons、score、issues、review_url。提供当前记录和过期记录的is_current标记；客户端只能使用当前有效结果。score没有产生或已失效时null，不把过期分数作为当前结果。报告元数据没有文件路径，敏感URL签名和令牌不得返回。

模型通过review_url引导用户复核，不能获取用户CSRF令牌或直接提交人工确认。授权用户页面将由后端另行实现。

### export_analysis

输入task_id及expected_revision，不接收输出路径、任意内容或目标URL。读取当前结构化结果，生成Markdown/CSV/JSON；未知可导出但须注明。P0不依赖模型生成任意新文件：报告文字由模板与已存解释组成，保持事实/计算/假设区别。

返回3个Artifact，格式各唯一：markdown/csv/json，包含artifact_id、format、media_type、filename、download_url、revision、sha256。下载入口还须验证同一用户/任务授权，不能把知道ID当作授权。示例文件/哈希是模拟值，没有实际下载地址。

## 5. 错误码

| code | HTTP | 默认retryable | 行为 |
|---|---:|---|---|
| INVALID_INPUT | 422 | false | 未声明字段、坏结构、ID/记录组合不合法 |
| UNAUTHORIZED | 401 | false | 缺少或无效任务令牌 |
| FORBIDDEN | 403 | false | 令牌无权访问任务/操作 |
| NOT_FOUND | 404 | false | 已授权范围内登记对象不存在 |
| FOREIGN_EVIDENCE | 403 | false | 引用不属当前任务，拒绝整批，不泄露来源 |
| INVALID_PDF / ENCRYPTED_PDF | 422 | false | 文件损坏或加密，用户处理 |
| FILE_TOO_LARGE / PAGE_LIMIT | 413 | false | 超过50MiB/300页限制 |
| NO_TEXT_LAYER | 422 | false | P0无OCR支持，提示用户 |
| PARSE_FAILED | 500 | false | 整体解析失败；局部用partial+issues |
| INVALID_UNIT / INVALID_YEAR | 422 | false | 不能解释的单位或与原文年份不一致 |
| REVISION_CONFLICT | 409 | false | 读取最新结果后重新判断，不盲目重试 |
| IDEMPOTENCY_CONFLICT | 409 | false | 同key请求体不同，客户端修正 |
| RULE_VERSION_MISMATCH | 409 | false | 规则版本不符，重读规则 |
| INVALID_STATE | 422 | false | 非法人工状态、N/A或与已确认结果冲突 |
| MODEL_UNAVAILABLE | 503 | true | 模型阶段暂不可用，客户端统一有界重试 |
| TIMEOUT | 504 | true | 同key恢复，不能假定未写入 |
| INTERNAL_ERROR | 500 | false | 默认不重试，服务明确判定瞬态时可true |
| BUDGET_EXCEEDED | 429 | false | 保留结果停止，不能用自动重试突破额度 |
| CANCELLED | 409 | false | 停止自动处理，恢复前核对当前任务 |

代理/网络层无法得到HTTP响应时，插件生成同形ToolResult，task_id为已知当前任务，revision未知则null。FastAPI默认422的detail数组必须被后端转换成统一信封，不直接暴露堆栈。

## 6. 示例、验收与变更

examples中每个工具各有request/success/error。**各三件套是独立场景，不是30个文件串成一条运行记录**：例如compare的comparable示例假设已有服务端确认，calculate_score示例单独假设C01=met、C02=partial，其余unknown，不从get_task_results示例推断。

schema.json是领域数据契约，不是后端实现、完整OpenAPI或Harness现成插件。自动测试只验证结构与少量样例算术。证据真实性、任务归属、数据库事务、幂等持久化、规则锚点和计算正确性须在后端实现后按backend-acceptance-cases.json补测。

新接口的接入顺序：extract_metrics → validate_metrics → get_task_results，再用search_evidence核对原文，然后接比较、规则和评分。真实运行验证与模拟契约验证必须分别记录。
