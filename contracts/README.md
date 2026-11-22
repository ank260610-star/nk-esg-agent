# ESG工具接口契约 v0.2.0

状态：**已定义并通过契约校验，业务后端和Harness工具尚未实现。** 示例全部是虚构模拟数据，不代表真实企业、已提取结果或真实API响应。

## 双方从哪里开始

1. 阅读[接口规范](TOOL_SPEC.md)，确认工具、字段、HTTP映射和错误处理。
2. 以[tools.json](tools.json)查找10个工具的请求/响应Schema引用。
3. 使用[schema.json](schema.json)中的对应`$defs`校验输入输出。**根文件是定义集合，不能直接用根对象验证请求**；须选中manifest指定的`$ref`，同时保留`$defs`。
4. 插件成员使用[examples](examples/)内每个工具的request/success/error文件编写适配；后端成员按同样结构实现。
5. 执行下方检查。通过只说明Schema和模拟示例自洽，不表示服务端权限、证据或真实模型已验收。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r contracts/requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -p test_contracts.py -v
```

Python最低3.11。Windows可使用`.venv/Scripts/python`。这是契约校验环境，不是尚未实现的应用启动方式。

## 文件

| 文件 | 作用 |
|---|---|
| TOOL_SPEC.md | 双方共同遵守的字段、行为和传输规范 |
| tools.json | 工具名、HTTP路径、Schema引用、写入与幂等标记 |
| schema.json | JSON Schema Draft 2020-12，严格禁止未声明字段 |
| examples/*.request.json | 每个工具的一份输入，共10份 |
| examples/*.success.json | 每个工具的一份成功输出，共10份 |
| examples/*.error.json | 每个工具的一份失败输出，共10份 |
| backend-acceptance-cases.json | 后端实现后必须补测的业务案例，当前未执行 |
| requirements-dev.txt | 契约校验依赖 |
| ../tests/test_contracts.py | 结构、危险输入与模拟数据一致性测试 |

旧`search-evidence.mock.json`与`error.mock.json`已同步新结构，只为兼容旧文档保留；新开发优先使用下划线工具名的30份示例。

## 与原交接方案的补充

本目录细化原[实现方案](../docs/superpowers/plans/2026-11-19-esg-agent-implementation.md)。冲突时，本目录对**工具与传输字段**的定义优先：

- 写入型parse_report/extract_metrics/compare_years/save_check_proposals增加必填expected_revision；export_analysis原有该字段保留。
- extract_metrics接收`{expected_revision,metric_ids,years}`，指标候选由后端生成、校验并保存；主智能体不再提交候选。检查建议使用`{expected_revision,checks}`，task_id来自URL。
- 新增只读`GET /api/tasks/{task_id}/rulebook`，供get_rulebook使用。
- 输出data统一为有明确名称字段的对象；search返回`{evidence,issues}`，不再直接返回数组。
- MetricCandidate只用于后端内部提取器边界，不是Agent输入；后端验证后注入服务端ID/revision/review_state。提取结果包含后端run版本、模型/提示词版本及用量。
- 自动CheckProposal只允许met/partial/not_met/unknown；已有人工N/A可在结果中读取，但不能由模型再次提交。
- coverage/weight使用0—1十进制字符串；score使用0—100；percent_change使用百分数值，例如25%传`"25"`。

接口变更应同时修改规范、Schema、manifest、示例、提示词和测试，由另一端成员复核后合并；不静默另起一套字段。本次为破坏性职责调整，契约升为0.2.0；评分规则仍为disclosure-v0.1。旧save_metric_candidates工具及其请求/输出示例已移除，不作为Agent兼容入口。
