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
| `@deepseek-ai/dsh-session` | 0.0.1-rc.5 | `JsonValue` 类型来源 |
| `typescript` | 5.9.3 | devDependency |

## Harness SDK / runtime / 模型（✅ 2026-11-22 实测锁定）

| 项 | 实测值 |
|---|---|
| `deepseek-harness-sdk` | `0.2.2rc2`（PyPI 官方源 `--pre` 安装；第三方同名 `deepseek-harness` 不是它） |
| `deepseek-harness-runtime-bin` | `0.2.2rc2`（win_amd64 wheel，86,959,936 B，SHA256 `a791af95...e33e9aea`） |
| SDK 包名/导入 | pip `deepseek-harness-sdk`；`from deepseek_harness import DeepSeekHarness`（类名与 README 一致） |
| 有效 profile | `sdk`（完整工具系统；`sdk-minimal` 无工具注册能力） |
| 模型 ID | `deepseek-v4-flash`（真机工具调用实测可用；README 示例名，但实测通过故采用） |
| provider | `deepseek-official` |
| dsh_home / cwd | `D:\esg-agent-runtime\dsh-home` / `D:\esg-agent-runtime\workspace`（隔离，禁落用户目录） |
| 启动命令 | 见 `scripts/smoke_gate_a.py`：`DeepSeekHarness(dsh_home=..., cwd=..., profile="sdk", provider="deepseek-official", model="deepseek-v4-flash", env={"DSH_PERMISSION_MODE":"read-only","ESG_API_BASE_URL":...,"ESG_AGENT_TOKEN":...})` |
| 插件加载 | `dsh plugin --profile sdk add file:<绝对路径>` + profile `cordis.patch.yml` 加 `- insert: [{id: nk-esg-tools, name: nk-esg-harness-plugin}]` |
| 实测结果 | 工具调用链 + 权限红线全部通过（见 decision-log.md 第 2 节）；16+11+3 测试全绿 |

## 有效权限配置（✅ 实测）

- `sdk` profile 的 `read-only` 命名预设生效（system-reminder 实测显示 "Current DSH file policy: read-only"），
  官方 `packages/bundle/base/cordis.patch.yml` 定义三个预设：read-only / workspace-write / danger-full-access。
- 默认拒绝白名单：插件 `src/index.ts` 用 `ctx.tools.guard()` 单调拒绝非 `ALLOWED_TOOLS` 的工具；
  闸门 A 阶段白名单仅 `get_task_results`。实测 pwsh/web_fetch 被拒、恢复会话仍拒、白名单工具可用。
- `read-only` 只约束文件系统，不替代任务令牌与后端 HTTP 权限（handoff §3.4）；业务权限靠 guard + 令牌。
- ⚠️ Windows 下 `pwsh-sandbox` 对 read-only 仅 partial enforcement，且网络不在 SandboxMode 词汇表——
  因此 Shell/网络拦截依赖 guard，不依赖沙箱（已实测 guard 有效）。
- 遗留 P1：模型可见工具清单仍含约 30 个内置工具（执行被 guard 拦截但 presentation 层可见），
  若需对模型隐藏用 `ctx.tools.restrict()`（agent scope），闸门 A 未做。

## 2026年11月23日本机接入补充

Mac arm64 实装 SDK 与 runtime 均为 0.2.2rc2，实际启动与新建会话 cwd 检查通过，未调用模型；真实持久对话恢复未验收。当前插件为十工具白名单；作用域 restrict 可用，实装 runtime 无 presentAs 方法。历史 Windows 闸门记录保留，不能作为本次接入的真实模型证据。配置与验证范围见 [接入说明](live-integration.md)。
