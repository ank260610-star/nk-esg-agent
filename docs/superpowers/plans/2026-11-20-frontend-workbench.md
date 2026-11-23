# ESG工作台前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 按用户确认的三栏草图搭建可本地运行的页面与完整演示交互。

**Architecture:** FastAPI/Jinja2提供页面和静态资源，浏览器管理明确标注的演示状态；真实解析、Harness、评分与持久化等待业务后端。提供固定公开样例PDF的受控只读入口，用户选择的本地PDF仅在浏览器预览。

**Tech Stack:** Python、FastAPI、Jinja2、Uvicorn、原生HTML/CSS/JavaScript。

**Spec:** 本聊天中确认的三栏布局及 docs/superpowers/plans/2026-11-19-esg-agent-implementation.md 中的页面约束。

## Global Constraints

- 仅codex分支；保留已有AGENTS.md；不自动提交或推送。
- 服务绑定127.0.0.1；不接入收费模型，不发布公网。
- 模拟数据、人工参考摘要和真实PDF分别标注；未知不转0。
- 人工复核必须有理由，修改后旧评分和导出过期；页面演示修改仅保留当前会话。
- 不实现业务后端契约，不宣称完成真实提取、模型问答或评分。

## Review Focus

- 任意上传PDF不能套用比亚迪参考值；独立本地预览状态并提供返回示例入口。
- 用户问题/文件名/复核理由通过textContent渲染，不执行HTML。
- 复核后禁止导出旧结果；更新快照保留人工修改，未知不默认满分。
- 无PDF样例文件时说明缺失，仍可查看明确标注的参考摘要。
- 小屏、键盘、弹层关闭与焦点返回均可用。

### Task 1: 本地页面宿主与数据边界
**Files:** pyproject.toml、backend/src/esg_app/{__init__,main}.py、tests/test_frontend.py。
**Interfaces:** create_app() -> FastAPI；GET /、/health、/demo/report、/demo/report-status；PDF入口只访问固定公开案例路径。
- [x] 编写并运行页面/静态资源、固定PDF缺失、Host拒绝测试；初始运行被缺依赖阻止，安装后验证实现。
- [x] 实现仅提供页面的本地服务，安装隔离依赖并通过测试。

### Task 2: 三栏工作台与演示交互
**Files:** backend/src/esg_app/templates/{base,index}.html、static/{css/style.css,js/app.js,js/demo-data.js}。
**Interfaces:** 浏览器ES模块导出demo数据；脚本控制分析状态、页签、证据面板、复核、问答与MD/CSV/JSON下载。
- [x] 实现响应式三栏：报告/任务、状态/问答、指标/评分/报告。
- [x] 实现分析进度/取消、本地PDF预览、逐年证据复核、版本失效与同快照导出。
- [x] 浏览器验证主流程、恶意文本、缺失值、窄屏、取消与键盘交互。

### Task 3: 文档和交付
**Files:** README.md、backend/README.md、docs/frontend-workbench.md。
- [x] 写明启动方式、模拟能力边界、接口接入点和验证结果。
- [x] 运行原有契约检查、页面测试、JS语法检查与浏览器验收；审查完整本次差异。

## 完成记录

20项Python检查通过；JS语法与数据检查通过。浏览器验证桌面1440px和手机390px、分析与取消、复核版本失效、三格式实际下载一致性、PDF预览隔离与文本安全。未提交/推送；未修改既有AGENTS.md。
