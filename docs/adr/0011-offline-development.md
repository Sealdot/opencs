# ADR-0011: 资源受限时先做离线开发

Status: Accepted — development workflow

## Context

维护者明确说明本机空间长期不足，要求拆分任务并通过模拟继续，避免安装部分依赖。
本次授权调整开发与验收方式，不改变 D1–D6 领域语义或 PostgreSQL 的生产参考选择。

## Decision

按 Chatwoot 离线 Spike → Voice 事件离线 Spike → V0.1 可离线实现任务的顺序继续。
本地复用已有 Python 环境，新增模拟代码仅使用标准库和现有 Schema 校验依赖；不安装 Docker、Chatwoot、LiveKit、模型权重或数据库服务。
脚本化 Fake Agent 返回预设动作，Fake Tool 返回合成账单，模拟输出写入本地审计；SQLite 标准库用于小体积实验日志，不作为 PostgreSQL 兼容性证明。

模拟器放在 `experiments/offline/`，不将其标为已发布 Runtime。真实 provider 的代码/配置与离线替身边界分开，未来可以在其他具备资源的环境执行真实验收。
原 kit 不变；原计划中的「真实 Spike 通过后才开展任何下游工作」调整为「离线测试可继续开发，真实集成是正式版本验收门槛」。

## Alternatives

等待本机升级会阻塞不依赖硬件的工程工作。云主机可提供真实集成，但目前没有必要开通付费资源或新服务。
只把真实测试标记跳过而不提供离线断言，无法证明任何业务行为，故不可采用。

## Consequences

可测试会话映射、权限拦截、人工接单规则、幂等输入、合成故障和评估断言。
不能据此证明 Chatwoot API/鉴权兼容、LLM质量、真实语音/延迟/打断、媒体转接、PostgreSQL事务并发或分布式故障恢复。
模拟与真实结果分开记录；正式 V0.1 Exit 仍须真实集成、数据库与容器验证。

## Maintainer Impact

不要求用户腾空间、装容器或配置密钥才能继续日常开发。单一轻量模拟命令可作为回归入口。
