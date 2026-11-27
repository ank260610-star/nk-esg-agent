# PR12 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留 V4 观测证据年度，拒绝跨年复核；防止 Compose 展开 bcrypt 哈希后仍通过预检。

**Architecture:** 在隔离适配和业务证据持久化两处传递 column_year，沿用现有复核校验。部署模板使用单引号字面值，预检同时核对 Compose 实际插值环境，不输出秘密值。

**Tech Stack:** Python/FastAPI/SQLite、pytest、Docker Compose/Caddy。

**Spec:** 本次 PR #12 审核的 P1（跨年写入）和 P2（bcrypt 插值）两项意见。

## Global Constraints

- 复用 codex 和 PR #12，保留其他工作区修改。
- 不部署公网，不调用真实模型，不修改既有资料或业务副本。
- 无新增应用依赖；Compose/Caddy 检查只在部署工具链进行。

## Review Focus

- 同一报告跨年证据必须拒绝且无记录、版本、审计、幂等残留。
- 同年证据修正可成功，重启后年度仍存在。
- 检查与目标证据没有唯一年度时保留 null，不猜测年份。
- 未加保护、双引号哈希或 shell 覆盖不能静默通过预检。
- Compose 或网关检查失败不得泄露完整环境及密码哈希。

### Task 1: 年度约束

**Files:** Modify `backend/src/esg_app/import_v4.py`、`backend/src/esg_app/beta_workspace.py`；Test `tests/test_import_v4.py`、`tests/test_beta_workspace.py`。

**Interfaces:** `adapt(package)` 的观测 evidence 增加 `column_year: int | None`；`prepare_workspace` 原样写入 task_parses，复用 `_evidence_supports` 的年度比较。

- [x] 新增适配器年度回读、同报告跨年 400 无写入、同年 200 及重启保留测试。
- [x] 运行新增测试，确认原实现失败。
- [x] 传递年度，运行导入与业务副本回归。

### Task 2: 部署字面值与检查

**Files:** Modify `deploy/env.example`、`scripts/preflight_beta.py`、`docs/private-beta.md`；Test `tests/test_beta_workspace.py` 和新增 `tests/test_beta_deploy_config.py`。

**Interfaces:** `check(env_path)` 仍返回不含秘密的 configuration_ready；仅接受单引号 bcrypt 字面值，并通过 `docker compose ... config --environment` 比较实际配置，异常只输出固定说明。

- [x] 测试未保护／双引号哈希拒绝、单引号成功、shell 覆盖拒绝、工具失败不泄密。
- [x] 运行新增测试，确认原实现失败。
- [x] 实现模板与预检；用合成哈希验证 Compose 插值及本地 Caddy 配置／认证。
- [x] 跑全量 Python、Node 和插件检查，复查完整 PR，更新验证说明。
- [x] 按用户授权提交到 codex 并更新 PR #12，不合并或发布。

按用户已授权范围在本会话直接执行；不分派子智能体。
