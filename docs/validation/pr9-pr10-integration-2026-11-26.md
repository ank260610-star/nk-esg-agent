# PR #9 / #10 审核与合并联调记录

日期：2026-11-26。验证对象是本地隔离开发副本，未使用真实模型或真实业务数据库。

## 基线与集成方式

| 对象 | 提交 |
|---|---|
| main / PR #8合并点 | 4043669094ac490684bcc4e862054b30d8207543 |
| PR #9源分支 codex-metric-review-v02 | fc421819e62fb66a3aaaa33e7cab6539e83abdf0 |
| PR #10集成前 codex | a130089d8b481216e90d43545c1238b559018f84 |
| 本地集成合并提交 | e4858eb（保留PR #9原作者提交） |

PR #9合入codex，后续审核修复也在codex；更新原PR #10。未修改队友源分支，未合并main。PR #10因此包含PR #9全部提交，两者不再作为互不依赖的独立最终版本。

## 审核发现与修复

| 等级 | 触发场景与风险 | 修复与证据 |
|---|---|---|
| 高 | 已登记证据不足后，经指标确认/修正或旧检查接口修改目标，指纹失效，限制可能消失并回落到confirmed | 失效处置阻塞完成；人工修正版沿supersedes_id保留未解决限制；评分仍unknown；页面与三格式导出保留needs_recheck。集成测试覆盖三个入口 |
| 高 | PR #10确认只检验证据ID归属，可能确认无数值依据或错年度记录 | 两条指标入口共享证据支持校验；不支持的999、错column_year拒绝 |
| 中 | PR #9删除证据文本全部空白，可能把年度与数值拼接 | 数字匹配使用原文，单位别名匹配另行规范化；正例与拼接反例回归 |
| 中 | 缺失标记按子串匹配，单位中的/可能冒充缺失；conversion_rule文字可放行单位不等 | 缺失标记受控且独立出现；原值/标准值数值一致、单位受控别名等价；错误分母与数量级拒绝，任意换算暂不支持 |
| 中 | PR #9未阻止运行期间新增复核，且owner读取位置与PR #10不一致 | 新请求检查agent_run_claims；已成功幂等重放优先；两接口统一检查所有已声明owner/user_id位置 |

为避免限制永久无法关闭，新增显式resolve处置：要求理由和新增任务内证据ID，禁止普通confirm或只重用旧证据解除限制。系统只验证结构、归属与现有事实支持；补证是否实质解决范围/方法问题仍须人工判断，不能据收到文件自动解决PEND。

## 验证结果

- 集成后、修复前：原有351项测试通过，但新增跨接口测试暴露上述问题。
- 最终Python：**363 passed，30 subtests passed，无跳过**。存在一项依赖弃用警告（FastAPI TestClient/httpx），不影响测试结果。
- 新增跨接口集成测试12项：确认与修正后限制保留、旧检查入口不可解除限制、证据不足/错年度拒绝、运行互斥与幂等、补证解除、三格式导出与评分、归属冲突、复合单位与数量级。
- Node：**5项通过**；business.js语法检查通过。
- 插件后台桥测试包含在Python全量回归中，无跳过；本次未修改插件实现。
- git diff --check通过。

复现命令（使用已安装项目依赖的Python环境）：

```sh
python -m pytest -q --tb=short
node --test tests/test_business_client.mjs tests/test_frontend_data.mjs
node --check backend/src/esg_app/static/js/business.js
git diff --check
```

## 兼容性与仍待验收

- review-dispositions GET保持current为有效处置，另加pending供展示未解决限制；history保存历史。导出review_dispositions包含有效处置与仍存在的限制。CSV新增review_needs_recheck、review_source_target_id，消费方须识别检查处置行。
- 数值修正接口已联调；工作台的数值修正表单尚未接入，按钮明确提示界面待接入。人工处置表单已支持resolve。
- 无column_year的表格证据仍需人工对照原页核验年度列；自动文本匹配不等于表格语义或材料实质审核。
- 未声明owner/user_id的任务仍采用本地单用户原型权限边界，不能作为生产多用户权限验收。
- 未调用真实模型、未修改真实任务/PEND、未执行批量导入或导入映射dry-run；未宣称Task 7、真实持久会话恢复或Task 8通过。
- 推荐将更新后的PR #10作为集成审核对象；若先合PR #9，再合PR #10，原提交已包含在其祖先中。不要另行复制或重复cherry-pick同一功能。
