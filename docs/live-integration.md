# 真实模型与 Harness 接入说明

当前已接入十个领域工具、后端 DeepSeek 提取适配器和后台 SDK 宿主。离线跨语言联调与实际 SDK 初始化已验证；没有配置 DEEPSEEK_API_KEY，因此尚未发起真实模型请求，不代表 Task 7 或 Task 8 已验收。

## 新环境准备

从仓库根目录执行，Python 至少 3.11，Node 至少 22。SDK 可选依赖固定为 deepseek-harness-sdk 0.2.2rc2，并安装同版本平台 runtime。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,harness]'
npm --prefix harness-plugin ci
npm --prefix harness-plugin run build
npm_config_cache="$PWD/data/npm-cache" npm_config_store_dir="$PWD/data/pnpm-store" \
  npm exec --yes --package=pnpm@10.29.2 -- \
  .venv/bin/python scripts/prepare_harness.py \
  --home data/harness/home --workspace data/harness/workspace
```

prepare_harness 安装本地构建，核对 profile 中的三个 JS 文件与构建逐字节一致，另写调用级 patch，不覆盖已有 profile patch。应使用专门的 home 和 workspace；二者不能相同或互相包含。不要把插件的开发依赖、API 密钥或任务令牌复制到仓库。

将 .env.example 复制为 data/local.env，在本机填写 DEEPSEEK_API_KEY，核对其中的 home、workspace、patch。data 目录已被 Git 忽略。uvicorn 工厂本身读取进程环境；以下脚本负责把配置文件按字面 KEY=VALUE 加载，不执行 shell，不解释插值。

```bash
.venv/bin/python scripts/run_task7.py --env-file data/local.env --check
```

退出码 2 与 status=blocked 表示缺配置；输出只列变量名，不回显密钥。preflight_ready 仅表示配置可启动，不能作为模型有效、余额充足或整链通过的证明。

## 真实运行与暂停恢复

首轮使用已核对来源的 PDF，不把测试金标准加入模型输入。例如：

```bash
.venv/bin/python scripts/run_task7.py --env-file data/local.env \
  --pdf /绝对路径/比亚迪2024可持续发展报告.pdf \
  --company-id byd --company-name 比亚迪 --report-year 2024 \
  --years 2023 2024 --output data/task7/byd-run-001
```

输出目录必须是新目录。脚本在独立线程启动回环业务服务，SDK 在调用线程运行，防止工具回调死锁。报告文件只由宿主登记；模型只得到任务绑定、允许的工具参数和受控证据。运行产生 task.sqlite3、run-summary.json 及实际答复；默认不把这些私有运行文件提交 Git。

completed 需要正常 SDK 回合、服务端当前结果调用及当前导出证据；完整分析还要求当前指标和二十项检查均通过人工状态门槛。未确认或未知结果进入 waiting_review。退出码 0 也可能表示到达复核暂停点，不能直接解释为 Task 7 整链验收通过。error、blocked、max-tokens、interrupted 或空输出不得当作成功。

继续查看同一任务、复核检查和恢复 SDK 时，使用原数据库及同一组 SDK 目录：

```bash
.venv/bin/python scripts/run_task7.py --env-file data/local.env \
  --serve --database data/task7/byd-run-001/task.sqlite3 --port 8000
```

浏览器访问 http://127.0.0.1:8000/。用户接口 POST /tasks/{id}/agent-runs 接收 mode=resume 与当前 expected_revision，复用最近 waiting_review 的 session_id。运行状态、同任务占用和后端调用审计保存在 SQLite，重启不自动清除。正在运行但 worker 已退出的状态必须核对真实调用后由可信宿主处理，不按超时直接重跑。

当前复核页面主要支持检查项；指标人工确认端点仍需补齐，必须包含理由、版本比较、审计和派生失效。不要由模型修改 review_state，也不要临时改数据库来伪装整链完成。需要继续跑到最终完成时，先解决这一业务复核入口。

## SDK 探测

```bash
.venv/bin/python scripts/probe_harness_runtime.py \
  --home data/harness/home --workspace data/harness/workspace \
  --patch data/harness/home/esg-tools.patch.yml
```

该探测启动真实 SDK 和插件、创建会话并检查 cwd，不调用模型。resume_checked=false 是正常的新会话探测结果。只有存在真实首轮产生的持久对话后，才用 --resume-session-id 复测恢复；仅初始化目录的空会话不作为持久恢复证据。

锁定 runtime 支持 agent 作用域 restrict，当前 npm 类型包中出现的 presentAs 不存在于该 runtime 实装接口中；插件没有调用它。新建与恢复时通过 agent/created 安装作用域白名单，另用全局单调 guard 拒绝原生工具。实际模型可见清单与越权尝试仍应在 Task 7 真机记录中检查。

## 提取与传输边界

后端按请求指标检索当前任务证据，并附带已登记的文本、表格、脚注和实际 ID。请求固定发送到官方 HTTPS 接口，拒绝重定向，不接受模型选择地址、模型或提示词。JSON 传输对象为 candidates 数组的单键包，候选仍逐条按原契约校验。空数组、截断、格式错误或未提供的证据 ID 按失败处理；数字、单位及无验证换算无法与引用证据核对时降为 unknown 并记录 MODEL_VALUE_UNVERIFIED，不能当未披露。

成功与失败尝试保存实际提供的 token 用量；未知保持 null，版本冲突也保留已发生用量。主 SDK 模型用量尚未与后端调用上限统一，现有 max_extraction_model_calls 仅限制每任务后端提取调用。正式真实验收需单列主模型、提取模型用量与总额度，不能把未知报为零。

插件只向回环业务原点请求，拒绝重定向、任意路径和模型字段注入。写入 UUID 由 token、任务、工具与宿主 session/call identity 生成；同一调用的最多两次客户端重试保持同键、同请求。业务 4xx 不自动重试；取消、断连或超时均不保证远端回滚。恢复后的新宿主令牌不是旧调用的自动重放凭据，存在未决调用时必须先核对业务状态。

原三栏前端仍为独立演示入口，不应作为真实业务链验收证据。本地 X-User/cookie 不是生产认证，服务维持回环绑定。

## 验证与来源

先构建插件，再运行 pytest，才能覆盖 Node 客户端到实际 FastAPI 的十工具联调。该测试明确使用离线 provider 替身，不等于真实模型自主调用。

官方接口和 SDK 依据：

- https://api-docs.deepseek.com/api/create-chat-completion/
- https://api-docs.deepseek.com/quick_start/pricing/
- https://github.com/deepseek-ai/deepseek-harness/blob/master/python/sdk/README.md

当前配置使用官方文档推荐的 deepseek-flash；实际模型版本以未来真实响应记录为准，不用历史别名推断锁定模型版本。本机实装 SDK 0.2.2rc2 的 API 与初始化结果优先于其他版本文档中的新增方法。
