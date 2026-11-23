# 统一代码基线

## 开发基线

- 开发分支：`codex`；共享依据为 `origin/codex` 的最新已验证提交。
- 业务模块来自智能体交付 `73f16b7`；持久解析契约来自 `7a53ad4`。
- 已合入主分支 `5351685` 的历史，保留原三栏前端演示。
- 准确提交号使用 `git rev-parse HEAD` 查看；同步后核对 `git status --short --branch`。
- 新同学检出或切换到 `codex` 并快进同步；已有未提交工作先备份、提交或妥善合并，不能强制覆盖。

## 应用与资源

| 用途 | Python 入口 | 模板 | 端口 |
|---|---|---|---|
| 业务服务/服务端页面 | `esg_app.main:create_app --factory` | `templates/` | 8000 |
| 三栏演示 | `esg_app.frontend_demo:app` | `demo_templates/` | 8765 |

业务代码仍统一位于 `backend/src/esg_app/`；两种入口共享包内静态资源目录，但不互相覆盖模板。
前端演示保持原有交互与数据边界，未连接业务 API 或模型。业务解析能力和既有模块限制见
[解析验收说明](parse-report-acceptance.md)，启动方法见根 README 和[前端说明](frontend-workbench.md)。

## 验证

在统一工作副本执行：

```sh
python -m pytest -q
node tests/test_frontend_data.mjs
node --input-type=module --check < backend/src/esg_app/static/js/app.js
node --input-type=module --check < backend/src/esg_app/static/js/demo-data.js
```

本次 Python：201 项测试、30 个子测试通过；前端数据与脚本语法检查通过。
新增前端文件直接来自原目录；仅调整 Python 模块入口与独立模板路径，业务接口及解析器未改动。
原目录未提交文件已备份并逐文件核验不变。
插件 `npm run check`、`npm run build` 与 `npm test`（3项）通过；
wheel 构建、两套模板与静态资源完整性、从 wheel 加载两个应用的冒烟检查通过。
Task 7 真实 SDK 整链、真实 PDF 回归和 Task 8 第二报告仍需独立验收。
