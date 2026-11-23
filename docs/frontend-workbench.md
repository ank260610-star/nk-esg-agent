# 环境披露工作台：前端原型

2026-11-20，按队长确认的HTML/CSS/原生JavaScript与FastAPI/Jinja2方案实现。仅本地运行，不部署公网；本轮没有接入真实业务后端、模型或Harness。

## 入口与业务基线

本演示在代码基线对齐时保留为独立入口 `esg_app.frontend_demo:app`，默认端口8765；业务服务为 `esg_app.main:create_app --factory`，默认端口8000。两者拥有独立模板，前端演示没有接入真实业务API。正式业务服务的持久化解析契约见[解析验收说明](parse-report-acceptance.md)。

## 启动

在仓库根目录，Python 3.11+：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-frontend.lock.txt
.venv/bin/python -m uvicorn esg_app.frontend_demo:app --app-dir backend/src --host 127.0.0.1 --port 8765
```

浏览器打开 http://127.0.0.1:8765 。Windows可将`.venv/bin/python`替换为`.venv\Scripts\python.exe`。

`pyproject.toml`定义应用依赖范围，`requirements-frontend.lock.txt`记录本次页面与测试使用的完整依赖版本。此锁不代表Harness SDK/runtime已经锁定。

## 已实现的交互

- 桌面三栏：报告与任务、分析进度/问答、指标/评分/报告；窄屏改为上下排列。
- 可选择2023/2024年，至少保留一年；六指标保留字符串精度、尾随零和缺失标记。
- 运行/取消模拟分析，进度可见；流程完成后仍明确标记无真实模型调用。
- 点击指标打开侧边原生dialog，选择年度、查看PDF物理页/印刷页和人工参考摘要。
- 浏览器预览本地PDF，仅检查扩展名、大小（≤50MiB）和PDF文件头；不检验页数、加密或完整性，不上传、不解析、不保存。此处不是正式上传接口。
- 独立选择的本地PDF不显示比亚迪结果，真实分析按钮禁用；可返回公开样例继续演示。
- 人工复核必须填写理由；保存后演示版本递增，旧评分/导出失效。更新快照保留人工修改，检查项保守地全部置为unknown，参考分为空、覆盖率0%、区间[0,100]；不假装已重新评分。
- 三个预设问答：Scope 3缺失、参考分与证据页码；其他问题明确尚未连接模型。所有输入按纯文本展示。
- Markdown/CSV/JSON浏览器下载，绑定同一演示版本，含来源与模拟标记；CSV缺失值空白，JSON数值字符串/缺失null，CSV公式前缀转义。
- 所有复核与聊天只保留在当前页面内存，刷新重置；没有业务数据库或用户认证。

## 示例与PDF

六指标来自实现交接文档第6节的人工参考值，不是自动提取。20检查项的初始状态与75分来自同节手算示例，不是对比亚迪的实际评分。摘要明确标为人工参考，绝不伪装PDF逐字引用；复核修改不覆盖原始参考摘要。

公开PDF原件由`GET /demo/report`只读提供，只检查以下两个固定路径，优先第一个：

- `data/reports/比亚迪_2024年度可持续发展报告.pdf`
- `队友筛选/发给候选人/比亚迪_2024年度可持续发展报告.pdf`（当前机器既有样例）

该端点不接受路径参数。其他机器按`docs/DATA_SOURCES.md`准备公开PDF；缺文件时`/demo/report-status`明确返回false，页面提示原件缺失，仍可查看标注后的人工参考摘要。浏览器PDF能力各异，提供新窗口链接、物理页码和手动定位提示；页面不保证内置查看器自动跳页。

PDF公开来源：https://static.cninfo.com.cn/finalpage/2025-03-25/1222881489.PDF

## 后端接入位置

当前服务只提供`/`、`/health`、`/static/*`、`/demo/report-status`和`/demo/report`。没有实现或冒充`/api/*`契约。域名只允许localhost、127.0.0.1与IPv6本机地址；默认启动仅绑定127.0.0.1。

- `demo_templates/index.html`：页面结构和表单。
- `static/css/style.css`：布局、断点与视觉样式，不加载外部字体/CDN。
- `static/js/demo-data.js`：集中存放人工参考数据与手算示例；正式接入后替换该数据源。
- `static/js/app.js`：`runDemo()`模拟进度；`openEvidence()`/`renderEvidence()`证据视图；复核表单事件与`updateSnapshot()`演示版本状态；`answerQuestion()`预设回答；`snapshot()`/`download()`演示导出。
- `frontend_demo.py:create_app()`：页面宿主，后续可挂载业务API。

正式接入按contracts v0.2.0添加独立API客户端，以服务端revision、指标、评分、任务状态和导出artifact为准；移除演示计算/下载路径。登记上传、SDK运行触发和人工复核走用户会话，模型密钥不得放入浏览器。用户运行接口仍须按交接第3.3节与后端另行定稿；不要将本原型的内存状态视为持久化或权限验收。

## 验证

```sh
PYTHONPATH=backend/src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_frontend_data.mjs
node --input-type=module --check < backend/src/esg_app/static/js/app.js
node --input-type=module --check < backend/src/esg_app/static/js/demo-data.js
```

本轮20项Python检查通过（16项原有契约＋4项页面宿主）；JS数据检查覆盖独立状态、保留精度、缺失值和CSV公式转义。浏览器人工验证运行完成/取消、证据页码、年度选择、复核与版本失效、未知项快照更新、三种实际下载、PDF本地预览隔离、纯文本输入与响应式布局。

下载后另读三份实际文件，确认同为v2、人工修改保留、E03历史值在JSON为null且CSV为空、失效检查项保持unknown。上述检查不代表真实模型、提取准确率、SDK权限或业务评分已经验收。
