# ESG业务后端

负责PDF登记解析、证据检索、六项指标提取、结构化校验、跨年计算、自建评分、复核和导出。已合入智能体侧模块与业务页面，parse_report 已接入持久事务；其他模块仍有内存仓储与契约缺口，尚未完成真实整链验收。

Python代码统一位于backend/src/esg_app/，依赖配置在根目录pyproject.toml；根目录tests、rules和contracts不变。业务页采用FastAPI/Jinja2；其中harness_runner.py是智能体成员维护的SDK宿主适配，不承担指标提取逻辑。SDK必须在后台执行，不能阻塞接收工具回调的HTTP服务。

## 对智能体交付什么

- extract_metrics接收任务、版本、指标和年份；后端自行检索证据、调用提取模型/规则、校验并保存。
- 返回带证据/页码的MetricRecord、issues和提取run版本及用量；未知不当0，缓存不覆盖人工修改。
- 提取候选结构只在后端内部使用，不给主智能体开放任意指标写入。
- 模型参数、提取提示词、预算和缓存由后端维护。主智能体负责调用、检查建议和解释，不能重复搭建另一套指标抽取流程。

提取模型约束见[prompts/metric-extraction.md](prompts/metric-extraction.md)，正式字段见[接口规范](../contracts/TOOL_SPEC.md)及Schema。当前模板不代表已经配置模型或通过真实抽取验收。

参考项目按模块借鉴：Tracker的指标抽取/单位后处理与人工验证、ChatReport的证据检索、EulerESG的指标—证据组织。复用前核对许可、中文适配和原文追溯；不把三个完整系统拼成依赖前提。

第一轮交付：运行extract_metrics，并用search_evidence核对其记录；与插件成员联合验证同一契约。

## parse_report 服务端契约

已接入任务令牌授权、SQLite 任务版本/解析证据持久化、原子幂等提交与文件哈希/解析器版本缓存。
运行、宿主登记与验收边界见[解析接入与验收](../docs/parse-report-acceptance.md)。
本次接入不代表其他工具已全部满足持久化契约，也不代表真实 SDK 整链或第二报告已验收。

## 前端演示入口

原三栏前端以 `frontend_demo.py` 与 `demo_templates/` 保留，运行 `esg_app.frontend_demo:app`；
业务服务继续使用 `main.py:create_app --factory` 和 `templates/`，二者不互相覆盖。
两者当前没有业务API集成，详见[前端说明](../docs/frontend-workbench.md)。
