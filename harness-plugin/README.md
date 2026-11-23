# Harness工具插件与智能体

负责官方DeepSeek Harness的Python SDK宿主接入、10个领域工具适配、任务编排、披露检查建议、证据追问与结果解释。当前已实现十工具插件、回环传输与宿主绑定；真实模型整链尚未验收。

指标提取模型/规则与提取提示词属于业务后端。插件调用extract_metrics取得带证据结果，不再向主模型暴露指标候选写入工具。输入输出以contracts/v0.2.0为准，评分规则仍独立为disclosure-v0.1。

- 系统提示词：[prompts/esg-agent.md](prompts/esg-agent.md)
- 使用说明：[智能体提示词使用说明](../docs/prompts/智能体提示词使用说明.md)
- 工具契约：[contracts](../contracts/README.md)

SDK宿主薄适配位于backend/src/esg_app/harness_runner.py，由智能体成员维护；业务页面使用FastAPI/Jinja2，Harness Web UI仅作调试。新会话与恢复会话均须验证隔离home和10工具白名单，read-only不能代替后端权限。11/21前的真实工具闸门及失败处理见实现交接文档v0.3。

先用标记mock的接口示例跑通编排，再切到真实后端。不得把模拟结果当生产证据；记录锁定Harness版本、模型ID及真实工具调用。复核只由用户页面执行。

配置、构建、实际 SDK 初始化与验收边界见[接入说明](../docs/live-integration.md)。
