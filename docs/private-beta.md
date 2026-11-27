# 受保护公网内测：发布操作手册

本轮交付的是上线准备代码与可验证的业务副本，不代表已发布。首轮内测允许查看、人工复核、PDF和导出下载；模型调用预算固定为0。禁止匿名访问，不允许上传任意文件，不提供公开注册或共享团队密码。后续启用模型调用需要独立预算与调用计量验收。

## 访问架构

Caddy终止HTTPS并使用bcrypt密码哈希验证独立账号。认证后覆盖X-ESG-User，注入随机网关密钥；应用仅信任该组合。客户端X-User、Cookie和Authorization不传给后端。app服务没有宿主机端口映射，不能绕过网关公开8000端口。

公网模式默认拒绝未归属任务。任务页、复核、PDF、导出、运行状态统一做所有权校验，多个owner/user_id声明必须一致。网页不再自动写local_user cookie。写请求必须带与配置完全一致的Origin；所有请求体上限1MiB（网关额外限制1MB），每个账号每分钟120次请求，计数持久化到SQLite。所有页面与下载no-store，配置CSP、HSTS及禁止嗅探等响应头。

Basic认证用于小范围私有内测：账号由管理员在网关维护，每位审核人使用独立账号与密码管理器生成的长随机密码。当前模板只配置一个账号；增加账号时在Caddyfile的basic_auth块添加独立bcrypt条目，并逐个明确任务归属。浏览器可能缓存Basic凭据，撤销账号应移除/替换对应哈希并重载网关，不能把关闭页面当作退出登录。正式多用户产品应另接组织身份提供方与账户生命周期管理。

## 准备业务副本

在可信离线工作区运行，不对现有业务数据库apply：

```sh
PYTHONPATH=backend/src python scripts/prepare_beta_workspace.py /path/V4.zip --output /path/new-beta-data --owner actual-reviewer
```

目录必须不存在。适配器先校验V4，再生成绑定该账号的12条核心指标和20条检查，复制登记的比亚迪PDF。所有指标unreviewed，资料中的检查建议保存在source_check_proposals，正式检查初始全部unknown，不把参考答案当人工确认。吉利测试答案与宁德资料不进入业务任务。

4条源quarantine_pending及1条pending缺失标记完整保留在source_import_observations；页面显示“导入时的待确认记录（原始审计快照）”。12条核心指标中有4条关联未决事项，以system_import的reopen处置阻止普通确认绕过限制，不冒充人工核查完成。当前有效状态由后续人工处置决定，原始审计快照不自动改写。

PDF摘录导入标记reference-package-v4/partial，不宣称完成逐页PDF语义审核。E02单位只归一受控分子别名，分母不改；分母与边界仍未知，比较继续受限。目标不自动选定，最终评分仍为空。

## 配置与预检

1. 使用已有授权服务器与域名，把域名解析到服务器。仅开放80/443，app端口不得映射到公网。此处不创建云资源或产生费用。
2. 将生成目录复制到服务器专用数据目录。Linux上需让容器UID 10001可读写该目录及文件，其他无关用户不可访问。停止服务后再复制/备份SQLite，或使用SQLite在线备份API；不要在写入中直接复制数据库文件。
3. 复制deploy/env.example为deploy/.env，文件权限设为600。填写真实域名、审核账号、bcrypt哈希、数据目录和至少32字符的随机网关密钥。网关账号必须与manifest.json及数据库owner相同。不要放入模型API Key。
4. 密码哈希通过交互式命令生成，避免把密码放在命令行历史：

```sh
docker run --rm -it caddy:2-alpine caddy hash-password
python scripts/preflight_beta.py --env-file deploy/.env
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml build
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
```

真实凭据不提交仓库。不要公开`docker compose config`的完整输出，它可能包含环境密钥。部署后固定已验证的镜像摘要；当前Caddy 2标签用于构建准备，不作为可重复发布的锁定镜像。网关配置依据Caddy官方basic_auth、reverse_proxy和request_body文档。

## 实机发布门槛（未完成前不放行）

- 容器构建、Caddy配置校验、真实域名证书获取均成功；HTTP跳转HTTPS。
- 无凭据和错误密码均401；正确账号可进入自己的任务，伪造身份头无效。
- 第二个独立测试账号无法读取第一个账号的任务、PDF、导出或运行状态。
- 人工复核后刷新、评分/过期状态及三格式导出一致；停止重启后记录与导出仍可读取。
- pending没有被普通确认消除；目标保持未选，测试答案未进入业务任务。
- 超限体积413、超频429，模型入口403；数据目录、数据库与密钥不可由静态路径访问。

这些是服务器与真实网关验收，不由TestClient测试或Compose解析代替。账号、域名、服务器访问与部署授权由队长提供后，才执行实际公开发布。

## 回滚

保留发布前数据备份与上一版镜像摘要。发现鉴权或数据问题先停止gateway，恢复上一版镜像和对应数据库副本，再做登录/跨账号/下载烟测。不要强制重置代码或覆盖唯一数据副本。升级前备份business.sqlite3、artifacts、PDF和manifest，凭据单独备份。
