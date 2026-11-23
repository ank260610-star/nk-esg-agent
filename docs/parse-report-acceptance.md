# parse_report 服务端接入与验收

## 基线与范围

在 `codex` 合入智能体交付 `73f16b7aa24c4a5a462bd69d69e69f9e214f7eda`。
两侧 Git 历史独立，合并保留双方文件及 `.gitignore` 规则。
本次于本机独立副本重建云端未推送的修改，提交号将与原 `00e26ee` 不同。
原项目目录中的未提交前端文件未被覆盖，也未混入此提交；其后续同步须保留并另行整合这些修改。

本次补齐服务端解析契约，不代表 Task 7 真实 SDK 整链或 Task 8 第二报告已验收。
其他工具仍保留既有内存仓储与接口缺口，没有在本次整体迁移。

## 安装与启动

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m uvicorn esg_app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Windows 将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。
默认使用运行目录 `data/esg.sqlite3`；实际部署建议以 `ESG_DATABASE_PATH` 指定固定绝对路径。
显式传入 `Settings()` 的测试应用默认使用独立内存 SQLite，避免测试污染业务数据库。
任务、令牌摘要、登记报告路径、解析摘要、解析证据、缓存及完整幂等成功响应持久化。
指标/复核/导出等其他仓储仍为原交付实现，不能宣称全部业务结果可重启恢复。

## 可信宿主登记

任务创建与上传界面尚未实现。已有文件只能由可信宿主登记，路径不向模型开放。
在服务停止时，用与应用相同的数据库配置初始化；令牌由宿主生成，从环境读取，不写入代码或日志：

```python
import os
from esg_app.parse_repository import ParseRepository
repo = ParseRepository(os.environ['ESG_DATABASE_PATH'])
repo.register_report_path('report_001', '/controlled/reports/report.pdf')
repo.register_task('task_001', 0, {'report_ids': ['report_001']})
repo.issue_agent_token('task_001', os.environ['ESG_AGENT_TOKEN'])
repo.close()
```

重复登记已有任务会报错，不覆盖历史版本。报告文件由宿主管理，用户不能任意修改登记文件。
兼容既有 `app.state.report_paths` 注入方式，首次解析成功时会将该路径一并保存，供重启后查看。
文件不存在时幂等重放已成功响应仍有效；新的解析请求返回脱敏 NOT_FOUND。

## 接口

`POST /api/tasks/{task_id}/parse`：

- `Authorization: Bearer <任务令牌>`。
- `Idempotency-Key: <宿主 UUID>`；该字段不是模型参数。
- JSON 仅含 `expected_revision`，必须为非负整数，拒绝布尔、字符串和额外字段。
- 成功信封与 ParseSummary 沿用 contracts 0.2.0，不新增模型输入字段。

兼容 `/agent/tool` 的 `parse_report`，沿用 `X-Agent-Token` 和同一幂等头；arguments 仅含 expected_revision。
任务取自令牌绑定；两个入口使用同一幂等域。TS 插件尚未注册此工具，需在 Task 7 单独接入和验收。

鉴权后先查成功幂等记录，再检查版本；同 key 同请求重放原响应及原 revision，同 key 不同请求返回 409。
幂等域为令牌身份摘要、任务、工具和 key。解析期间若令牌撤销或改绑，提交时再次检查并拒绝。
解析放在线程池执行，期间不持有数据库写事务。提交时重新检查幂等、版本及任务报告绑定。
SQLite 在同一事务中保存任务版本、报告路径、证据关联、摘要、缓存和完整成功响应。
失败整体回滚且不消耗 key；跨连接并发由数据库事务与唯一主键保护。
同 key 并发可重复做只读解析，但只提交一次业务变更，不重复生成证据。

缓存键为 PDF 内容 SHA256 + 实际 pdfplumber 版本及坐标解析器版本。
首次关联到任务递增一次 revision；已关联相同有效结果不递增、不重复创建证据。
解析器版本或文件内容变化会替换当前任务解析证据并递增版本。
解析摘要独立保存，不向既有 get_task_results 数据中夹带额外 Schema 字段。
原人工复核入口也改为核对同一数据库版本，避免解析更新后接受旧复核请求。

## 内容与错误边界

保留原页全文，并复用已有坐标引擎产生年度行证据。印刷页未知时保持 null，不假定固定页差。
拆表失败仍保留可读取的全文，标记 TABLE_PARSE_FAILED；失败页不得当作未披露。
没有新增脚注语义识别；脚注仍可通过完整原页追溯，不能据此声称原报告 gold/脚注定位已重现。

- 部分空白页或失败页：200 + partial，保留可用证据。
- 两份报告中一份完全无文字：该报告 failed，整体 partial。
- 全部无文字层：422 NO_TEXT_LAYER；无可用页且有解析异常：500 PARSE_FAILED。
- 坏 PDF、加密 PDF、受控文件缺失或超限：整批拒绝，不提交半批业务结果。
- 限制：50 MiB、300 页；issues 按 Schema 上限最多返回 100 条，全文按 20000 字分块。

其他模块尚未整体满足 revision/幂等契约，真实模型编排前仍须审核其写入路径。
已有页面会话是本地单用户原型，不将此版本作为多用户生产权限系统部署。

## 验收

```sh
python -m pytest -q tests/test_parse_contract.py
python -m pytest -q
```

新增测试覆盖合成文字/无文字/加密 PDF、大小/页数上限、Schema、局部失败、双报告、缓存关联、
同键重放与冲突、旧版本拒绝、解析期间版本/绑定/权限变化、跨连接并发、事务回滚、重启恢复、
解析器版本替换、Agent 入口、检索可见性、PDF 查看及人工复核版本一致性。
既有 SDK 路径测试使用当前系统的临时绝对路径，在 macOS/Linux/Windows 验证同一隔离要求。

本机重建重新验证：197 项测试与 30 个子测试通过（Python 3.14）。
一个既有 TestClient/httpx 弃用提示不影响测试通过。不沿用此前云端测试成绩。
验证对象为合成 PDF，未完成比亚迪原始 PDF 回归、真实 Harness 调用或第二企业报告验证。
