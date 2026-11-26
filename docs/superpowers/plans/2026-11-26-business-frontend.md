# 真实业务前端第一阶段 Implementation Plan

> **For agentic workers:** Implement sequentially in the existing codex checkout; no delegation or paid model calls.

**Goal:** 在真实业务页完成指标证据展示、检查项复核、运行状态、同版本导出交互。
**Architecture:** 继续FastAPI/Jinja2 SSR，通过同源页面刷新取得完整快照；浏览器只调用已存在用户路由。独立business.js不加载demo-data，指标修正明确禁用。
**Tech Stack:** 现有Python、Jinja2、原生JS/CSS；不新增运行依赖。
**Spec:** 用户确认的第一阶段；桌面《前端Agent交付任务书_人工复核与真实业务接入.md》。

## Global Constraints
- 基于远端main bab1ff5，独立目录data/frontend-integration，分支codex；保留其他工作副本。
- 不调用Agent令牌API，不向页面发送令牌/模型密钥；不修改业务评分或指标复核契约。
- 不调用真实模型、不重跑整链、不公开部署；使用真实数据库副本和离线测试夹具验收。
- 无任务/未知/原文明示缺失/0/空值分别展示；以服务端revision为准。

## Review Focus
- 两窗口409保留草稿、显式刷新后用户决定，不自动重放。
- 超时结果未知不宣称失败回滚或重复执行；导出同次重试复用幂等键。
- 旧轮询不覆盖新任务；终态停止，待复核不是完成。
- 不可信文本转义，PDF链接只构造登记ID和正整数物理页。
- 当前导出仅认服务端is_current与revision；原始数值不舍入。

### Task 1: SSR三栏与真实数据
- [x] 添加web_presentation.py、业务模板和business.css，保留旧演示路由。
- [x] main.py只扩展SSR上下文与展示格式；测试无任务、0/null、证据链接、禁用指标复核、元数据和转义。

### Task 2: 用户交互
- [x] business.js封装JSON/HTTP错误、检查复核、SSR刷新、草稿、版本冲突、运行轮询、导出幂等与下载。
- [x] 指标/检查/评分页签、证据侧栏、原文预览与权限/缺失错误提示；不模拟进度。
- [x] 检查宿主缺失、待复核、失败和无取消接口边界。

### Task 3: 验证和交付
- [x] 全量现有测试、JS检查；浏览器离线运行夹具与真实任务副本验收。
- [x] 截图、接口映射、复现说明、限制；完整分支审核后提交并创建或更新PR，不合并。
