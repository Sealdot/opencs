# OpenCS

OpenCS 是 AI Agent 与客服系统之间的领域运行时：将会话、身份、工具权限、AI 与人工责任、审计和场景评估落实为可测试的代码。

**0.1.0a0：已发布 [GitHub prerelease](https://github.com/Sealdot/opencs/releases/tag/v0.1.0a0)，仅供离线开发预览，API 尚不稳定，不能用于生产客服。**
本轮仅收口已有 IM Runtime；Voice、生产数据库和新增供应商集成暂停。
公开源码采用[逐文件清单](docs/release/public-files.json)，不携带原始 kit、第三方 checkout 或上游贡献历史。
[许可与权属核对](docs/release/LICENSE_AUDIT.md)已记录维护者发布授权及有界 kit 来源追溯；该记录保留证据限制，不构成独立权属证明或法律意见。

## 10 分钟运行

适合想学习客服领域边界、验证工具权限与人工交接、贡献测试或文档的 Python 开发者。
需要 Python **3.12+**（本次验证为 3.12）；不需要 Docker、服务账号、API key 或模型下载。
获得候选源码目录后，在目录根执行。以下是 macOS/Linux 命令；Windows 激活路径为 `.preview\Scripts\activate`。

```sh
python3 --version
python3 -m scripts.build_package --out dist
python3 -m venv .preview
. .preview/bin/activate
python -m pip --isolated install --no-index --no-deps --no-build-isolation dist/opencs-0.1.0a0-py3-none-any.whl
cd ..
python -I -m opencs.examples.billing --fault none
```

正常输出包含 `passed=true`、`replay_unchanged=true`、`provider_calls=0`，退出码 **0**。
`-I` 确保使用安装后的包；示例可以从源码目录之外运行。
也可用相同 pip 参数安装 `dist/opencs-0.1.0a0.tar.gz`，本地 PEP 517 backend 无构建依赖。
若只收到 wheel，直接从 `python3 -m venv .preview` 开始并将安装参数改为 wheel 路径。
安装命令不访问包索引；首次创建虚拟环境需要 Python 自带的 `venv/ensurepip`。

```sh
python -I -m opencs.examples.billing --fault forbidden-tool
python -I -m opencs.examples.billing --fault tool-failure
python -I -m opencs.examples.billing --fault send-timeout
python -I -m opencs.examples.billing --fault none --output billing-report.json
```

三个故障场景应输出 `passed=false` 并退出 **1**：这是预期检测结果。
权限拒绝会在发送前拦截退款尝试，但该尝试仍使场景失败；工具失败不能满足 required tool；发送超时保留不确定结果，不盲重试。
故障演示请逐条运行，勿让 shell 的 `set -e` 在第一个预期退出 1 处终止全部验证。

## 当前架构

```text
入站事件 / API / Adapter
          ↓
application：显式 ports、权限、结构化决策、交接、审计
          ↓
domain：租户身份、会话、事件、handoff 生命周期

persistence：单进程内存 repository 与执行日志
adapters：已有 Chatwoot / Responses / HTTP 契约及标准库 transport
examples：合成账单数据、脚本化决策与 FakeTransport
```

Core 不导入供应商 SDK，Application 通过显式注入的接口调用 Agent、Channel 和 Tools。
运行时及构建 backend 只用标准库；测试/格式/类型工具是单独锁定的开发依赖。
[当前规格](docs/specs/)与[已批准 D1–D6](docs/bootstrap/DECISIONS.md)是领域规则来源。
`docs/specs/ARCHITECTURE.md` 的 Voice 与生产技术栈描述的是目标架构，不代表 alpha 已实现。
OpenCS 不替代 Chatwoot、LiveKit、CRM、STT/TTS 或通用 LLM 框架。

## 能力与准确边界

- 租户内显式映射、事件去重与冲突审计、确定性工具权限、出站责任检查、交接状态和场景断言可离线验证。
- repository、执行日志和幂等记录均在内存中；重启即丢失，不支持跨进程恢复、持久事务或横向扩容。
- 账单 demo 的数据、HTTP 响应与 Agent 决策全部合成。`provider_calls=0`、`model_inference=false`；不测真实模型选择能力。
- 已有 Chatwoot、OpenAI Responses 与 HTTP 代码只通过契约及本地模拟 HTTP 测试。真实鉴权、供应商兼容、人工队列和模型质量尚未验收，标记 `live_not_run`。
- `pending_human` 表示等待人工处理；handoff Requested 暂停 AI 客户输出。请求送达或 assignment 成功不等于人工已接单，不能推导 Accepted 或问题已解决。
- Policy Compliance、Tool Correctness、Handoff Quality 只覆盖有限离线证据；Resolution、Correctness、Grounding 在账单演示中为 `not_evaluated`，不计算总分。
- 没有生产数据库、真实 Voice/媒体链路、生产部署或生产安全支持。内存去重及故障注入不证明网络恢复正确。

已有可选 HTTP 服务的边界见[联调说明](adapters/HTTP_SERVICE.md)，本轮不要求配置或运行它。

## 开发与贡献

从[贡献指南](CONTRIBUTING.md)及[三个入门 Issue 草稿](docs/release/STARTER_ISSUES.md)开始。
每次只做一个可验收变更；涉及领域语义先按规格讨论。当前优先测试、文档和发布验证，不扩展架构或生产集成。

新开发环境（需要下载锁定的开发工具）：

```sh
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy opencs adapters scripts tests build_backend.py
uv run pytest
uv run python scripts/check_foundation.py --contracts-only
```

已有离线开发环境使用 `UV_OFFLINE=1 uv run --no-sync ...`。
公开源目录不含 kit/Spike；`--contracts-only` 显式验证 Schema/fixture，完整私人工作区默认命令继续校验原 kit。
[CI](.github/workflows/ci.yml)还会从干净 wheel/sdist 环境验证四个场景，并校验 sdist 重建 wheel 一致。
发布前手动触发同一工作流还会检查冻结快照哈希；日常 PR 可正常修改源码，新增公开文件须更新清单。

## 发布与许可

源码采用 [Apache-2.0](LICENSE) 声明；开发依赖保留各自许可证且不打包进 runtime。
原 kit 未提供独立授权证明；kit 衍生文档与自有代码的维护者授权及证据限制见[逐项核对](docs/release/LICENSE_AUDIT.md)。
[公开仓库](https://github.com/Sealdot/opencs)与 [v0.1.0a0 prerelease](https://github.com/Sealdot/opencs/releases/tag/v0.1.0a0)已于 2026-10-07 发布；该标签对应公开提交 `be9a2c2a9da389fb409e78a469083ee5f85abec2`。
该提交的 [push CI](https://github.com/Sealdot/opencs/actions/runs/37622160952)与[手动冻结快照 CI](https://github.com/Sealdot/opencs/actions/runs/37622178303)均已通过；详见[发布状态](docs/execution/STATUS.md)。
[Alpha Release 草稿](docs/release/ALPHA_RELEASE.md)与[公开步骤](docs/release/PUBLISHING.md)保留发布准备说明。PyPI 不在本次发布范围内，也不宣称正式 V0.1 退出条件通过。
