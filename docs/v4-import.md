# V4 候选资料包隔离导入

入口是包内唯一的《导入映射.xlsx》，不是单独一张证据表。此适配器只创建新的暂存库，不连接任务库，不调用模型，不生成最终评分，也不自动确认候选。

```sh
PYTHONPATH=backend/src python scripts/dry_run_v4.py /absolute/path/package.zip --output /absolute/path/new-staging-directory
```

输出目录必须不存在；重复使用拒绝，避免覆盖旧证据。输出包括 staging.sqlite3 和 summary.json。必需数据校验失败时不创建输出目录；错误退出码为2。输入为V4约定，增加企业或已选目标须单独扩展契约，不能静默推断。

## 登记与校验

- 从映射读取报告版本、观测、检查、目标候选、分流；按source_workbook/source_sheet/row_ref定位观测原文，按check_id回查正式检查源表，目标候选从映射登记原文及位置。
- 统一登记99条证据/参考项；观测、检查和目标证据分别标记kind，均保持unreviewed。推导参考并不是PDF原文证据。
- 检查报告ID、支持报告ID、源行ID、检查ID、证据冲突、引用、物理页范围、PDF哈希、规则内容、每家公司20个不同正式检查ID及分流一致性。
- 候选校验年度、有限数值、原始/标准数值相等、受控单位别名等价与来源字段；不支持任意换算，不自动把资料字段转换为正式MetricRecord。
- 严格解析true/false及1/0，字符串false不能当真；候选必须usable与usable_for_scoring均真且非pending。pending要求caveat、缺失材料及评分不可用。missing_marker保留原文标记和空值。
- 吉利观测只允许test_case；检查另存data_role=test_case。宁德报告仅注册版本，不导入观测或检查。暂存库和参考答案不作为模型输入。
- 包内规则必须与产品disclosure-v0.1完全一致；候选目标只接受needs_review，T项保持unknown/pending、空target_key，不能用candidate_target_key替代。

## 暂存契约

reports/evidence/observations/checks/target_candidates五张表保存来源payload。所有记录强制confirmed=false、review_state=unreviewed。observations的可用性标记写为JSON布尔值。

视图final_candidates、quarantine_pending、test_cases分别筛选对应观测；正式消费者不能直接把observations全表当最终答案。reference_only与missing_marker仍保留原分流。测试角色与业务角色不可合并作为生产结果。

当前未提供暂存库到正式任务库的apply入口。正式MetricRecord映射、派生关系绑定、评分权限、人工复核以及最终导入应独立验收。summary的final_score=null是未授权评分状态，不是调用评分引擎所得分值。

## 已知源文件边界

比亚迪指标明细AE10/AE11将“范围1+范围2；静态复算值”存成无缓存的公式。本版本仅对这两个确切位置和确切文字按说明文本读取并输出警告，从不执行公式；其他公式一律拒绝。建议资料侧将两格改为普通文本后重新交付。

4条吉利推导测试观测使用PNA、没有物理页码，仅以derived_reference保留空页码，不伪造原文位置，也不进入业务候选。正式启用这些派生值前应绑定组成观测与其证据。

源摘录、脚注与PDF的语义一致性未自动验证；映射成功不是原文正确或人工复核通过。缺失或冲突仍须资料侧定向补证。
