# 已采纳的领域决策 D1–D6

Status: Accepted — 2026-09-07。维护者在本任务中对「是否采纳 D1–D6 推荐方案」明确回复「采纳」。
批准范围为下表六项推荐方案，授权将其落实到规格、Schema 和回归检查；不表示真实集成已验证，也不自动接受其他 ADR 中的额外设计。
原始提案表与 Plan Only 保留作决策历史；表中的「待批准/尚未加入」描述的是批准前状态，当前实现见 `docs/specs/` 与 `docs/execution/STATUS.md`。
既有 kit 已指定单体、Python、PostgreSQL、provider-neutral Core、Chatwoot/LiveKit；这些不是重新征求技术栈许可。

| 决策 | 推荐方案 | 替代方案与代价 | 阻塞范围 |
|---|---|---|---|
| D1 会话与身份边界 | V0.1 用 `(tenant, provider, external conversation id)` 查显式映射；首次创建 Core Conversation，后续沿用。外部 contact 仅用于该租户内的显式 Customer 映射；不按姓名/邮箱自动跨渠道合并。已映射会话的关闭/重开保持同一 Core id，业务解决状态单独事件表示。 | 按时间窗/意图新建需会话切分规则；自动身份合并有误合并风险。 | TASK-102/103；批准后补 domain spec |
| D2 接手责任 | Requested 起暂停新的 AI 面向客户的输出；仅明确人工接单证据触发 Accepted，首个人工服务动作触发 InProgress；provider assignment 响应只表示请求送达。Failed/Cancelled 不自动恢复 AI，需显式恢复决策。每个 conversation 最多一个活动 handoff，以版本校验处理竞态。 | assignment 即 Accepted 简单但可能无人负责；失败自动恢复可能与人工重叠。 | TASK-005/108；需先更新 Handoff Spec |
| D3 生命周期事件 | 保留原有状态和箭头；补 handoff_in_progress / handoff_failed。Failed → Requested 保留 handoff id，增加 attempt 及新 event_id。每次 attempt 保留历史，不覆盖旧结果。过期回调不可回滚当前状态。 | 每次 retry 创建独立 handoff 需关联父记录；删减状态会改变已有 spec。 | TASK-004/005；当前 schema 尚未加入新增事件 |
| D4 首个账单场景 | reason 统一为 refund_requires_approval；重复扣费调查仅允许 get_customer/get_invoice，禁止 issue_refund；退款一律交人工审批，resolution=pending_human 不算 resolved。实际退款在人工外部流程完成。示例规则只放 examples 配置。 | 复用「金额≤20自动退」需要币种、金额精度、权限来源和审批语义，超出首例。 | TASK-006/107/109/110 |
| D5 事件与重试 | tenant 从已验证集成配置解析，不能信任 webhook 中自报的 tenant；持久幂等键 `(tenant_id,event_id)`。同键同 payload 重放返回原结果，同键不同 payload 拒绝并审计；入站事务先落库再处理。出站有稳定 action id 与发送前审计；超时不盲重试，先核对供应商状态。 | 仅 Redis 去重会增加基础设施且无法保证长期记录；对所有超时直接重试可能重复发送。 | TASK-103/104；候选 ADR-0007 |
| D6 场景评估 | Required tool 必须执行成功，Forbidden tool 连调用尝试都禁止；required handoff 要记录有效请求与 reason，人工接单另作集成断言。确定性断言必须全过；六项指标逐项记录 pass/fail/not_evaluated，不合成总分。 | 只看 LLM 文本易误判；模型裁判需要版本、评分尺度和成本策略。 | TASK-006/110；当前 schema 只做结构验证 |

## Handoff TASK-005 Plan Only

Affected files：`docs/specs/HANDOFF_SPEC.md`、`docs/specs/EVENT_SPEC.md`、候选 event schema、后续 handoff schema 与 fixtures。

状态：沿用 Requested → Accepted → InProgress → Completed；Requested 可 Cancelled/Failed；InProgress 可 Failed；Failed 可重试回 Requested。原规格不允许 Accepted → Cancelled/Failed，先保留，不暗加边。

待新增语义：D2 的责任/并发规则，D3 的 attempt、事件、旧回调拒绝机制。Accepted 阶段故障、人工中止需求应形成独立后续决策，不能绕过状态机。

边界用例：重复请求、assignment 成功但无人接单、客户新消息、排队中取消、人工/AI 同时输出、超时回调、重试后的旧成功回调、跨租户伪造、请求后进程崩溃。

验收：每条允许边有案例；每条非法边被拒绝；交接 payload 包含原因、客户/组织、意图、优先级、summary、facts、已完成/待办动作、下一步建议；敏感字段仅进入有权限的人工上下文。

迁移：尚无数据库，无迁移风险；批准后给 schema 定版本，之后的字段/状态变更需要兼容性说明。

## 集成阶段还需提供的信息

进入真实 Spike 时再绑定：Chatwoot 测试实例/版本及账号、Inbox/可接单队列；LiveKit 项目/版本、WebRTC 或 SIP 范围、STT/TTS provider；LLM 测试配置。
通过本地环境变量或 secret 管理提供凭据，不写入仓库。当前没有联通或音频验证证据。
