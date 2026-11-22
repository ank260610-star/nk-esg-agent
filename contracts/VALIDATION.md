# 契约验证记录

日期：2026-11-20；契约版本0.2.0。环境：本地macOS，Python 3.14，jsonschema 4.26.0。最低目标Python 3.11，本轮未单独测试3.11环境。

```bash
python -m unittest discover -s tests -p test_contracts.py -v
```

结果：**16项测试全部通过**。

覆盖：

- 10工具、30份请求/成功/失败示例及2份兼容示例符合对应Schema。
- extract_metrics仅接受任务、版本、指标与年份；拒绝任意候选、数值、模型名和人工状态。
- MetricCandidate在后端内部验证Decimal、缺失不当0、unknown原因和来源；不再有主Agent候选写入工具。
- 后端提取run包含版本、模型/提示词、用量和记录ID；completed不能隐藏unknown/冲突/解析错误。
- 禁止物理页码0、未声明路径字段、自动人工确认或自行N/A；写入必带版本，提取范围有界。
- 成功/失败信封互斥、无权限错误不泄露版本、不可比不返回普通同比、参考分不伪装最终分。
- 模拟评分手算、维度和导出格式一致；主提示词工具名、HTTP映射及后端目录一致。

另有19项后端业务验收案例，标记pending_backend_implementation_not_executed；当前未执行，不能计入测试通过结果。

**未验证**：真实后端、数据库事务/幂等持久化、任务与证据归属、中文报告提取效果、模型调用预算、Harness适配及并发网络行为。Schema不能证明这些能力。所有mock_*和模拟哈希/链接仅用于接口调试。
