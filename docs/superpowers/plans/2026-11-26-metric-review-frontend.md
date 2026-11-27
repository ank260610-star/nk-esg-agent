# Metric Review Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 v0.2 指标确认、修正、审计并完成离线联调。
**Architecture:** 复用 SSR 与同源用户接口。保留字符串／null；未知提交保留原请求与幂等键，显式重试。
**Tech Stack:** FastAPI/Jinja2、JavaScript、pytest、Node test。
**Spec:** docs/接口契约_指标复核_v0.2.md

## Global Constraints

使用codex；保留原工作区；额度0；不合并main。

## Review Focus

零与null独立；409保留草稿；超时同键同体重试；替代记录不继承旧草稿；审计安全展示。

### Task 1: 请求构建与重试

Files: backend/src/esg_app/static/js/business-client.js、business.js；tests/test_business_client.mjs。
接口：buildMetricReview(fields)构建请求。
- [x] 测试字符串、null和确认不带correction。
- [x] 实现表单构建、草稿与原请求重试。
- [x] 运行Node测试。

### Task 2: 表单及审计

Files: backend/src/esg_app/templates/index.html、static/business.css；tests/test_business_frontend.py。
接口：POST/GET /tasks/{id}/metric-reviews。
- [x] 添加修正字段、同报告证据与理由。
- [x] 只读审计通过GET权限保护读取。
- [x] 验证保存刷新、旧版本和导出过期。

### Task 3: 回归与交付

Files: scripts/preview_business_fixture.py、docs/frontend-metric-review.md。
- [x] 修正夹具以支持正式接口。
- [x] 构建插件，运行Python/Node和插件测试。
- [x] 检查完整差异并记录限制。
