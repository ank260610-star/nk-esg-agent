# 运行时版本锁定记录（实测）

记录时间：2026-11-22（北京时间）｜对应 handoff v0.3 / 契约 v0.2.0 / 评分规则 disclosure-v0.1

## 本机基础环境（实测）

| 项 | 实测值 |
|---|---|
| OS | Windows（win32） |
| Python | 3.13.14 |
| Git | 2.55.0.windows.3 |
| Node | v22.22.2 |
| npm | 10.9.7 |

## Python 契约/服务依赖（实测，装在 `.venv`）

| 包 | 版本 |
|---|---|
| jsonschema | 4.26.0（contracts/requirements-dev.txt 固定） |
| fastapi | 0.165.0 |
| starlette | 1.10.0 |
| httpx | 0.28.1 |
| pytest | 9.2.0 |
| pydantic | 2.14.4 |
| uvicorn | 0.54.0 |

## TypeScript 插件依赖（实测，装在 `harness-plugin/node_modules`）

| 包 | 版本 | 备注 |
|---|---|---|
| `@deepseek-ai/dsh-tools` | 0.0.1-rc.1 | `package.json` 写 `latest`，**必须按此实测值锁定**，rc 阶段破坏性变更风险高 |
| `@deepseek-ai/cordis` | 4.1.0 | 同上 |
| `typescript` | 5.9.3 | devDependency |

## Harness SDK / runtime / 模型（⚠️ 未锁定）

| 项 | 状态 |
|---|---|
| `deepseek-harness-sdk` | **未安装**（本机 `DEEPSEEK_API_KEY` 未设置，未进行真机烟测） |
| 捆绑 `deepseek-harness-runtime-bin` | 未获取版本 |
| Harness 提交 / profile | 未实测；handoff §3.4 要求 SDK、runtime、TS 扩展与源码提交共同记录 |
| 模型 ID | 未锁定；官方当前列 `deepseek-flash` / `deepseek-v4-pro`，不得照抄 SDK README 示例名 |

## 有效权限配置（待实测填）

- 计划：标准 `sdk` profile 的 `read-only` 预设（官方 SDK README 确认该 profile 提供 read-only / workspace-write / danger-full-access 三预设）。
- 待验证：新建会话与恢复会话的工具清单、默认拒绝白名单、`tool-web`/`tool-pwsh` 是否被显式禁用。
- 注意：`read-only` 只约束文件写入，不能替代任务令牌与后端 HTTP 权限（handoff §3.4）。
