# 业务写入、比较和导出验收

业务写入与解析共用 SQLite 的任务版本和幂等表。`business_state` 保存指标历史、检查项、比较、提取运行与复核审计；`extraction_attempts` 独立记录执行占用和调用次数；`artifacts` 保存受控下载元数据。原有前端文件不变。

## 调用方式

| 功能 | 入口 | 请求 |
|---|---|---|
| 提取 | `POST /api/tasks/{id}/extract` | expected_revision、metric_ids、years |
| 比较 | `POST /api/tasks/{id}/compare` | expected_revision、older_record_id、newer_record_id |
| 保存检查提案 | `POST /api/tasks/{id}/checks` | expected_revision、checks |
| 导出 | `POST /api/tasks/{id}/exports` | expected_revision |
| 校验指标 | `POST /api/tasks/{id}/validate` | record_ids |
| 评分、规则、结果 | `GET /api/tasks/{id}/score`、`rulebook`、`results` | 无模型可传的权重或结果 |

这些入口使用任务绑定的 Bearer 令牌；四个写入/导出入口还要求宿主生成的 UUID `Idempotency-Key`。同名领域工具也可通过 `/agent/tool` 的 `X-Agent-Token` 调用；task_id 来自令牌，模型不能覆盖。十工具插件的后续接入与验证边界见 [真实接入说明](live-integration.md)；服务端可达不代表真实 Harness 整链已通过。

同键同请求精确重放原响应，包括原始 revision 和 usage；同键异请求返回 409。业务失败不保存成功幂等结果。提取调用前和提交前均检查版本；模型期间不持有数据库写事务。相同任务存在提取占用时返回 INVALID_STATE，不重复调用。

网页导出继续使用 `/tasks/{id}/exports`，表单版本字符串由宿主转换；工具 JSON 不接受布尔版本、模型配置或多余字段。网页未传幂等键时由宿主生成，显式网络重试应保留同键。非法检索参数按现有契约返回 422。

## 业务行为

- 提取年份必须属于任务 selected_years。每个请求组合有合法记录或带原因的 unknown，未知不写零；冲突候选保留，completed/partial 反映记录状态。
- 任务外证据整批返回 FOREIGN_EVIDENCE，不保存候选。候选报告与证据报告一致，表格证据的明确年度不得错配。模型边界标签不作为服务端确认；未实现程序重算的 derived/重述关系不直接入库，转为待核实记录和 issues。
- 当前人工记录优先，重新提取不覆盖。证据内容与提取器、模型、提示词及指标年份形成指纹；有效记录未变时复用，model_calls=0，不增加 revision。幂等重放保留原用量；未知 token 用量保留 null。
- 后端默认不配置提取器，未配置返回 MODEL_UNAVAILABLE，不能用空结果伪装模型成功。可信宿主注入 ExtractionDeps；提示词与配置不接受模型覆盖。每任务后端调用上限默认 12，可由 Settings.max_extraction_model_calls 配置；冲突和失败调用也占用额度。该计数尚未与真实 Harness 主模型费用统一，需在 Task 7 接入时核对。
- 比较不修改原记录，相同当前输入复用既有比较；跨报告须由已登记元数据证明同企业。kWh/MWh 先换算再计算；分母类型、币种、倍率、单位不同或方法/边界未确认时不输出普通同比。
- 检查提案整批验证、整批保存；不同人工决定不能覆盖，禁止自动 N/A；partial 需要规则锚点，目标检查须指向同一 target_key。理由的真实性及其语义是否满足全部子条件仍需原文复核。
- 复核更新、审计和 revision 同事务保存。重新解析使旧指标、检查、比较、评分及提取缓存失效；重新提取使相关检查与比较失效。评分只读当前检查，不增加 revision。
- TaskResults 从当前持久状态构造，过期记录带 is_current=false，旧评分不冒充当前结果；仅登记 report_ids 的旧演示任务无法构造完整 TaskResults，返回 INVALID_STATE。宿主须登记符合 contracts/schema.json 的 Task 与 Report 元数据，不猜填企业、日期或文件信息。
- 历史上限遵循结果契约：指标 500、比较 100、提取运行 100；超出时拒绝新状态，不截断已有证据。

## 导出与恢复

三种格式读取同一业务快照。文件先在未公开目录生成，最终授权与版本检查通过后，三份元数据及成功幂等记录同事务发布；失败不登记半包，常规异常会清理已生成文件。突然终止进程可能留下未登记的孤立文件，但下载接口只服务已登记 ID。

产物元数据持久化，重启后仍可下载和重放。旧文件保留，以数据库当前任务版本判断是否过期，通过页面与 X-Artifact-Current 提示。网页产物绑定导出身份；Agent 导出归本地 local_user，Bearer 令牌不能借用浏览器 cookie 调用复核或下载。X-User/cookie 仍是本地原型会话，不是生产级多用户身份认证与任务授权系统。

如果进程在提取期间退出，持久 running 占用不会自动过期。可信宿主应先核对 worker、模型调用记录与任务状态，再处理占用；未知调用不能直接当作零用量或自动重新执行。旧版本只有内存中的业务结果无法从 SQLite 自动恢复，已有持久解析数据可直接升级。

运行时使用已存在的 jsonschema 校验输入/输出，因此将其从开发依赖移为运行依赖；安装包附带契约镜像，并以测试确保与 contracts/schema.json 字节一致。

## 验证

本轮结果与源码指纹见 `docs/validation/write-tools-2026-11-23.json`。主要命令：

```bash
python -m pytest -q
python scripts/regress_byd_pdf.py --pdf /可信宿主登记的原件.pdf --output data/regressions/独立目录
node --test tests/test_frontend_data.mjs
```

新增测试覆盖幂等精确重放/重启/不同请求冲突、两连接并发占用、提交期间版本变化、失败调用记账与额度、人工保护、严格输入、检查批量拒绝、派生结果失效、导出生成失败清理/重试/过期下载与身份隔离。模型测试均为离线替身，不是实际模型准确率验收。

比亚迪原件验收仍保留 partial：p127 非目标包装表 NON_NUMERIC_TABLE_CELL、p130 非目标人员行 ROW_UNIT_UNRESOLVED。Task 7 真实 SDK 整链与 Task 8 第二报告未在本轮执行。
