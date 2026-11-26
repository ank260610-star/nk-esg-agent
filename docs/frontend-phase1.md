# 真实业务前端第一阶段交付

日期：2026-11-26。基线为已合并PR #7的main（bab1ff5d5a6ac4812772a5691256b39712ca410f）。本轮在独立目录`data/frontend-integration`的codex分支实现，保留原根目录和其他副本的修改。

## 完成范围

| 能力 | 当前行为 |
| --- | --- |
| 真实任务 | `/?task_id=...`服务端渲染任务、企业、报告元数据、SHA、revision；缺字段显示待补齐。不读demo-data，不在失败时回退模拟数据 |
| 指标 | 从有效记录逐条展示原值/单位、标准值/单位、提取状态与人工确认状态；不写死记录数量，保留0、尾随零和缺失标记 |
| 证据 | 使用任务绑定Evidence的report_id与正整数物理页生成同源链接，忽略原始viewer_url；侧栏显示原文/年份/印刷页与脚注关联，预检PDF不可用或权限失败 |
| 检查复核 | 逐项状态和理由，经既有用户端点保存；成功后重新SSR刷新全部版本/评分/导出。409保留草稿，显式刷新后由用户决定，不自动重放 |
| 运行 | 启动/问题/继续用户接口，服务端run_id轮询；排队和运行时防重复，终态停止。待复核单独提示，不当成整链通过；宿主未配置时禁用启动 |
| 导出 | 生成服务端MD/CSV/JSON，按revision与is_current展示当前/过期；下载前读取版本头和权限状态，附件响应提供安全编码的文件名，不在浏览器重新生成报告 |
| 界面 | 三栏工作台、页签、证据抽屉、键盘操作、窄屏纵向排列；错误/未知/空结果有独立反馈 |

## 未完成与依赖

- 指标确认/修正、上传创建任务、审计历史仍等待正式用户端点。本阶段对应按钮禁用，不假保存。
- 任务异步快照JSON接口仍未提供；当前通过同源GET页面刷新完整SSR快照，没有调用Agent令牌结果接口。
- N/A及其他复杂检查复核操作暂不接入；目前仅set_check_status。
- 无用户取消接口，因此没有假取消按钮。问题和运行流程没有接入前端本地模型循环。
- 真实SDK复核后恢复仍未在本轮执行；运行交互使用显式离线夹具验证，不能据此关闭Task 7。
- 既有X-User/cookie属于本地单用户认证，未声称可公网生产使用。本轮不调用模型、不发布公网、不修改原始任务。

## 逐接口映射

| 操作 | 路径/字段 | 页面处理 |
| --- | --- | --- |
| 读取/刷新 | GET `/?task_id=...` | 仅取`business-root`，验证任务ID和revision不倒退，更新全页表单版本并保留未提交草稿 |
| 检查复核 | POST `/tasks/{id}/review`；action、check_id、new_status、reason、expected_revision | 区分HTTP错误和ok=false；理由必填；成功重新取SSR，不本地递增版本 |
| 运行 | POST `/tasks/{id}/agent-runs`；mode、expected_revision、question | 只传用户数据，不传session_id或任意模型参数；有run_id后才查询，不将受理当完成 |
| 查询运行 | GET `/tasks/{id}/agent-runs/{run_id}` | 2秒起步退避至8秒；终态刷新；错误停止并提示手动刷新，不叠加写入重试 |
| 导出 | POST `/tasks/{id}/exports`；expected_revision、Idempotency-Key | 单次操作键与请求绑定，结果不确定时保留；版本变化不复用不同请求的旧键 |
| PDF | GET `/api/reports/{report_id}/pdf#page=N` | 只使用登记ID与物理页；原文通过textContent展示 |
| 下载 | GET `/api/artifacts/{artifact_id}` | 校验状态和X-Artifact-Revision/Current，使用服务端附件下载；旧版本有明确提示 |

所有请求使用同源cookie，不制造X-User，不向页面下发Agent令牌或模型密钥。响应顶层/数据内revision分别归一；HTML错误响应不当JSON成功。超时/断连不意味着远端回滚；先刷新，再由用户决定。

## 运行与复现

仓库根目录安装既有开发依赖后：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
ESG_HARNESS_ENABLED=0 ESG_DATABASE_PATH=/绝对路径/任务数据库副本.sqlite3 \
  .venv/bin/python -m uvicorn esg_app.main:create_app --factory --app-dir backend/src --host 127.0.0.1 --port 8766
```

访问`http://127.0.0.1:8766/?task_id=你的任务ID`。数据库副本需由可信宿主用SQLite backup生成，不覆盖原任务，PDF必须仍位于已登记的可用路径。不得将私有数据库、令牌、运行日志提交仓库。

不需要真实模型即可复现浏览器交互：

```sh
.venv/bin/python scripts/preview_business_fixture.py --port 8767
```

访问`http://127.0.0.1:8767/?task_id=offline_browser_fixture`，页面显式标“离线测试夹具”。夹具数据库位于临时目录，退出后清理；PDF为生成的测试页，非企业报告。启动暂停、resume完成、问题失败都是离线行为，未加载SDK或模型配置。

## 浏览器验收记录

- 真实比亚迪任务副本：12条记录正常读取，E02两条unknown保留，E03历史“/”不变0；20项检查建议；waiting_review与未配置宿主分别显示；PDF证据定位物理页127。
- 离线夹具：检查复核revision 7→8；三份旧导出标过期；刷新仍为8，原数据库没有被修改。
- 双窗口：旧revision 7提交返回409，草稿文本保留；显式刷新到8后草稿仍在，没有自动重放。
- 运行：running→waiting_review、用户resume→completed、问题→failed；服务端失败文本中的script保持普通文本，终态停止轮询。
- 精度与安全：0、0.1300、原文“/”与null分别呈现；不可信原文、复核理由和运行答复不能执行HTML；最终分0不会显示为未定。
- 下载：Edge实际保存MD/CSV/JSON并读取内容，三份同为revision 7；CSV为3条夹具记录，JSON可解析且revision=7。内置浏览器下载事件等待曾超时，因此用Edge完成落盘验证，未将等待超时写为通过。
- 响应式：桌面三栏，390px时页面及三栏均宽390px，无横向溢出；证据侧栏宽352px，PDF链接保持同一登记报告与物理页。
- 页面截图保存在本机可视化输出目录，不上传私有运行材料。

## 检查命令

```sh
npm --prefix harness-plugin ci
npm --prefix harness-plugin run check
npm --prefix harness-plugin run build
npm --prefix harness-plugin test
python -m pytest -q
node --test tests/test_business_client.mjs tests/test_frontend_data.mjs
node --input-type=module --check < backend/src/esg_app/static/js/business.js
node --input-type=module --check < backend/src/esg_app/static/js/business-client.js
```

跨语言测试必须先构建插件并允许临时监听本机端口；模型提供方是离线替身。首次受环境限制而不能监听时的失败不作为代码失败，也未把跳过当作通过，已在允许回环端口的环境重跑。

本轮最终结果：Python **303 passed、30 subtests passed**，无跳过；插件类型检查/构建通过，**11项插件测试通过**；前端Node检查**4项通过**。pytest仅有既有Starlette/httpx弃用提示，不影响用例执行。以上为离线与页面验收，不代表真实SDK会话恢复或Task 7最终验收。

影响文件：README；main.py的SSR展示上下文和附件响应头；web_presentation.py；业务模板；business.css、business.js和business-client.js；离线浏览器夹具脚本；两份业务前端测试；本说明与实施计划。旧演示应用、提取/评分/复核事务逻辑、模型配置及接口契约未改动。
