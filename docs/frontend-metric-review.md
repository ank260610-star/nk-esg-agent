# 指标复核前端交付

日期：2026-11-26。基线：PR #9 fc42181（含 PR #7/#8）。真实模型额度0。

## 已接入

- 每条当前指标提供仅确认／人工修正，年份与报告固定。
- 修正支持五态、原文值／标准值／单位、范围、方法、换算说明、分母和缺失说明。
- 数值以字符串传输；空值通过独立选项设置，不把零、空字符串、null混淆。
- 同报告、已知年度匹配的证据多选；理由必填。实际支持关系由后端验证。
- POST成功后重新读取SSR，更新全部表单版本及导出过期状态；替代记录用新record_id，不复用旧草稿。
- GET审计按后端用户权限读取，展示身份、时间、理由、证据和前后记录；全部文本安全渲染。
- 409保留草稿，显式刷新后再核对提交；不自动重放冲突请求。
- 不确定提交保留原请求体和UUID，禁用其他写入，显式点击同键重试核对结果。重试成功后刷新SSR。

## 验证

- Python全量328 passed、30 subtests；0 skipped（含跨语言桥）。
- 插件11项；Node前端5项通过。
- 新增SSR集成测试覆盖修正→新记录→审计→旧导出过期、旧版本409与同键重放。
- 浏览器离线夹具：确认revision7→8，修正8→9，0.1300精度及manual来源保留；审计显示两项。
- 两窗口：窗口A提交，窗口B旧版本409；刷新后版本更新且原理由草稿保留。
- 响应丢失夹具：首次业务已提交但返回503，显式同键核对后仅一个审计事件、版本只升一次。

## 复现

```sh
npm --prefix harness-plugin run build
npm --prefix harness-plugin test
python -m pytest -q
node --test tests/test_business_client.mjs tests/test_frontend_data.mjs
python scripts/preview_business_fixture.py --port 8767
python scripts/preview_business_fixture.py --port 8768 --uncertain-first
```

页面：`http://127.0.0.1:8767/?task_id=offline_browser_fixture`。夹具明确非真实企业，不加载SDK。

## 边界

- 尚未在原始历史任务上执行正式人工确认；本轮不构成Task7通过或真实resume验收。
- 权限继续沿用后端本地原型，未扩大到多用户或公网。
- 不确定请求缓存在当前页面内；提示用户保留页面，不能关闭后假定已撤销。后续如需跨页面恢复，应单独加入持久请求日志。
- 证据候选沿用SSR已加载证据（引用证据及最多20条上下文）；其他证据需后续搜索入口，不伪造证据ID。
- scope3_categories沿用当前记录，本表单暂不编辑类别列表。
- 无新增依赖。PR #9仍由队长安排合并；本地前端提交不自动发布。
