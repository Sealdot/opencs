# 真实 HTTP 与账单联调服务

这是 `0.1.0a0` 的联调入口：标准库 HTTP transport、单租户签名 webhook 服务与账单示例装配。真实 socket 闭环已用本地模拟服务验证；Chatwoot、模型、账单系统的真实鉴权及行为仍为 `live_not_run`。

## 配置并运行

需要 Python 3.12+，无需第三方运行依赖。准备 Chatwoot 测试实例的 account/inbox/team、相应 API token 和签名 secret，配置模型及两个账单查询服务；字段模板见根目录 `.env.example`。模型名称必须显式填写，没有 FakeTransport 或默认假模型兜底。

```sh
cp .env.example .env
# 在本机编辑 .env；值须符合 shell 赋值语法，有特殊字符时正确加引号。
# 仅加载自己审核过的配置文件，不要 source 外部提供的脚本。
set -a
. ./.env
set +a
UV_OFFLINE=1 uv run --no-sync python -m opencs.examples.chatwoot_service --check
UV_OFFLINE=1 uv run --no-sync python -m opencs.examples.chatwoot_service --port 8080
```

CLI 只读取已导出的环境变量，不自动加载 dotenv。`--check` 校验必需字段、ID、URL、凭据字符和超时配置，不创建 listener、不请求网络、不证明鉴权有效；成功退出 0，配置失败退出 2。配置错误仅报告字段名，不回显字段值。配置对象的 repr 隐藏三个 secret。

| 配置 | 含义 |
| --- | --- |
| `OPENCS_TENANT_ID` | 本实例可信租户；同时用于工具请求 header，要求非空可打印 ASCII，无空白 |
| `CHATWOOT_BASE_URL` | 实例根地址，可带固定部署路径；无 userinfo/query/fragment |
| `CHATWOOT_ACCOUNT_ID` / `INBOX_ID` / `TEAM_ID` | 三者均使用 `CHATWOOT_` 前缀，值为正整数 |
| `CHATWOOT_API_TOKEN` | 相应测试用户或 AgentBot 的 API 凭据 |
| `CHATWOOT_SIGNING_SECRET` | 与实际 webhook 来源匹配的签名 secret，见 [验签说明](CHATWOOT_WEBHOOK.md) |
| `CHATWOOT_HANDOFF_MODE` | 默认 `assignment`；`agent_bot` 增加明确打开会话步骤，见 [交接前提](CHATWOOT_HANDOFF.md) |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_KEY` | Responses 接口配置；base 默认 `https://api.openai.com/v1`，模型与 key 必填 |
| `BILLING_CUSTOMER_URL` / `BILLING_INVOICE_URL` | 显式绑定 `get_customer` / `get_invoice` 的 POST 地址 |
| `OPENCS_ALLOW_HTTP` | 默认 `0` 仅 HTTPS；受控本地 HTTP 测试设 `1`，不关闭 HTTPS 证书验证 |
| `OPENCS_HTTP_TIMEOUT_SECONDS` | 默认 10，有限正数；限制单次出站 socket 阻塞操作，不是 DNS 或整个请求的总期限 |

只启动**一个进程、一个 worker**，不要使用自动重载。多个 worker、重启或新建 service 都会丢失/分裂映射、审计、暂停与去重状态，可能重复外部动作。本实现没有持久 inbox/outbox、恢复 worker 或跨进程协调。

开发 listener 固定监听 `127.0.0.1`（`--host localhost` 也归一化到该地址），HTTP/1.0、每次关闭连接，无访问日志；不提供公网监听、TLS、lifespan、streaming 或 WebSocket。POST 需 Content-Length（缺失 411），重复/非法长度和 Transfer-Encoding 被拒绝；请求与响应各限 1 MiB，读请求的 socket 空闲超时 10 秒。运行时执行时间不受这个入站超时限制。线程数和进程内数据量未做生产容量控制。

已有经审核 ASGI 服务环境可显式使用工厂 `opencs.examples.chatwoot_service:create_app`，仍限单 worker；不需要为本地验证安装 ASGI server。若远端 Chatwoot 要访问本地入口，需要另行提供受控 TLS 转发地址，保留原始请求字节与签名 header。不要直接暴露这个开发 listener 作为生产服务。

## 路由与业务边界

- `POST /webhooks/chatwoot`：原始 body 验签 → 配置账户/inbox/tenant 映射 → Runtime。签名默认接受过去 300 秒和未来 30 秒，部署时需保持时钟同步。
- `GET /health/live`：200，仅表示应用能响应，标记 `integration_preview` / `process_local_memory` / `durable=false`，不探测供应商。
- `GET /health/ready`：固定 503 / `not_production_ready`，如实表示持久化与正式发布门槛未满足。不要把它用作此联调入口的自动重启探针。
- 不暴露通用 `/v1/events` 或人工生命周期变更入口；本轮没有增加人工接单判定规则。

账单示例仍使用包内 `policy.json`：仅允许查询客户/账单，禁止 `issue_refund`，退款转人工审批，结果为 `pending_human`。工具调用 POST JSON `{"arguments": {...}}`，携带 `X-OpenCS-Tenant` 和稳定 `X-OpenCS-Action`；返回有限 JSON 对象。模型只能给出工具参数，不能选择 URL、租户 header 或改写白名单。

工具 header 是路由/审计上下文，**不是独立鉴权凭据**。该通用工具 Adapter 还没有业务 token 注入能力；只接可限制网络访问的受控查询网关，并由网关鉴权、校验租户与 customer/invoice 的资源访问权限。模型参数不能作为资源授权依据。不应直接配置要求额外认证而尚未适配的公网业务 API。示例服务不具备退款执行能力。

## 传输与失败契约

`adapters.stdlib_http.StdlibHttpTransport` 必须显式导入和注入，不从 `adapters` 根包加载。HTTPS 使用系统信任和主机名验证；没有关闭验证选项。每次新建连接，无连接池、自动重试、自动重定向、环境代理或隐式鉴权。

请求只接受有限 JSON 对象，拒绝重复大小写 header 与调用方自设 Host/Content-Length/Transfer-Encoding/Connection。body 按 UTF-8 序列化。成功响应限 1 MiB，必须为合法 JSON 对象；空 204、重复键、非有限数、截断或超限均视为传输结果不确定。非 2xx 保留状态码并丢弃响应 body，避免在审计/错误中传播供应商敏感内容。

TimeoutError 或 TransportError 不表示供应商未执行动作。Channel 将其记录为 `unknown`；工具的 TransportError 转为 Runtime 已有的 unknown-and-stop 路径。失败/未知事件在当前进程重放返回已存结果，不新增外部请求。需另行核对供应商状态，当前没有自动恢复操作。Chatwoot 的逐步交接行为见 [CHATWOOT_HANDOFF](CHATWOOT_HANDOFF.md)。

Chatwoot `before_request` hook 现额外提供 Core `conversation_id`，用于在多会话中把每一步发送前审计归入正确的租户/会话；不包含 token 或请求 body。业务审计可能包含客户上下文，不是可任意公开的日志。

## 已验证与下一步

`tests/test_stdlib_http.py` / `tests/test_devserver.py` 验证实际 loopback socket、请求/响应边界及失败行为；`tests/test_chatwoot_service.py` 验证签名 HTTP 入站→模拟 Responses→查询工具→回复→私有上下文/团队/open 的真实本地 HTTP 闭环，重放不重复、AI 暂停、禁止退款、未知工具结果停止、错误脱敏及无网络配置检查。TLS 默认验证配置有单元测试，尚未以真实外部证书服务联调。

下一步在受控测试环境填写配置并运行：验证真实 Chatwoot 签名来源与 token 权限、实际模型的结构化输出、工具网关的资源授权，以及 AgentBot pending → open 的实际队列行为；明确人工接单仍需独立证据。记录脱敏版本、操作与响应到 integration-notes，不能用本地模拟测试替代这些门槛。
