# 闸门 A（Task 1）实测决策记录

记录时间：2026-11-22（北京时间）｜记录人：智能体技术成员（离线实施）

## 结论（先看这里）

**闸门 A：未通过（离线部分完成，真机工具调用未验证）。** 不把离线测试、mock 返回或普通聊天 API 记成 Harness 集成通过。

## 已完成（有测试证据）

| 项 | 证据 | 结果 |
|---|---|---|
| 源码就位 | codex 分支快照 57 文件（tar.gz 下载，Git 初始化并提交 `32352b4`） | ✅ |
| 16 项契约基线 | `.venv/Scripts/python -m unittest discover -s tests -p test_contracts.py -v` | ✅ 16 passed |
| 权限/宿主测试（新增） | `pytest tests/test_runtime_access.py tests/test_harness_runner.py -q` | ✅ 11 passed |
| TS 插件白名单测试（新增） | `npm test` | ✅ 2 passed |
| TS 插件编译 | `npm run build`（tsc） | ✅ 通过 |
| 契约字段对齐 | 实现前用 `schema.json` 的 `Error`/`get_task_results_output`/`TaskResults` 定义逐字段核对 | ✅ |

## 未完成（阻塞项）

| 项 | 原因 | 处理 |
|---|---|---|
| SDK → 模型工具调用 → TS → HTTP → 结构化结果 真机链 | `DEEPSEEK_API_KEY` 未设置；`deepseek-harness-sdk` 未安装 | 需队长/用户提供密钥与额度后补做，不得用 mock 冒充 |
| 工具事件 ↔ HTTP 请求脱敏对应记录 | 依赖真机链 | 同上 |
| 新建/恢复会话工具白名单实测 | 依赖 SDK 与 runtime 锁定版本 | 同上 |
| 通用 Shell/文件/网络工具拒绝实测 | 依赖 SDK 与 runtime 锁定版本 | 同上 |

## 明确的工程决策

1. **先写失败测试再实现**：新增 `tests/test_runtime_access.py`（7 项）与 `tests/test_harness_runner.py`（4 项），确认红灯后才写最小实现，未改动原 16 项契约测试。
2. **最小实现只做读路径**：后端仅实现 `get_task_results` 的契约信封与任务令牌隔离；未实现其余 9 个工具，未把 mock 当生产数据。
3. **默认拒绝白名单**：`Settings.allowed_agent_tools` 默认只含 `get_task_results`；插件侧 `isWhitelisted()` 同步收窄；模型夹带 `command`/`task_id`/`path`/`url`/`weight` 字段一律拒绝。
4. **任务隔离**：A 令牌访问 B 任务返回 403，且错误信封中 `task_id`/`revision` 为 null（不泄露任务存在性）。
5. **人工复核隔离**：Agent 令牌访问 `/tasks/{id}/review` 返回 403；复核只允许独立用户会话 + CSRF。
6. **状态诚实**：`AgentRunStore.complete()` 拒绝空答案；SDK 异常只写 failed，不写 completed。
7. **SDK 宿主解耦**：`run_agent()` 延迟导入 SDK，缺密钥时抛 `RuntimeError`，不返回模拟结果；`dsh_home` 与 `cwd` 必须显式且隔离，绝不回落到用户家目录。

## 真实接入时必须补做的验证（handoff §7 Task 1）

- [ ] 装 `deepseek-harness-sdk`，记录 SDK 版本、捆绑 runtime 版本、Harness 提交。
- [ ] 用隔离 `dsh_home` 与任务 `cwd` 加载插件，配 `read-only` 预设 + 默认拒绝白名单。
- [ ] 真机触发一次 `get_task_results` 工具调用，保存 tool 事件 ↔ HTTP 请求脱敏对应记录。
- [ ] 新建会话与恢复会话均验证工具清单与权限。
- [ ] 通用 Shell/文件/网络/子 Agent/权限变更工具被拒；合法业务工具可读后端。
- [ ] 超时取消不导致重复写入；SDK 异常不标 completed。
- [ ] 全部通过后，才把闸门 A 改判为「通过」并进入 Task 2。

## 已知限制

- 当前后端是内存仓储（`InMemoryTaskStore`），不是 SQLite 事务实现；revision 递增、幂等、审计日志留待完整实现。
- `BusinessClient` 当前打 `/api/tasks/current/results`（占位路径），真机接入前需改为服务端按令牌上下文解析 task_id 的内部路由，不能让模型填 task_id。
- 插件 `output.schema` 用 `additionalProperties: true`，只覆盖信封顶层字段；真机前应按契约 `TaskResults` 完整定义收紧，防止输出被截断后误判。
