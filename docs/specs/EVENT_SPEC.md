# Customer Service Event Specification

Baseline: 0.0，2026-09-07；批准依据为 D3/D5。机器格式见 [event.schema.json](schemas/event.schema.json)。

## 1. 目标

把 Chatwoot webhook、LiveKit transcript、Email、Tool callback 等转换成统一事件。

## 2. Envelope

```json
{
  "event_id": "evt_01",
  "tenant_id": "tenant_acme",
  "conversation_id": "conv_01",
  "customer_id": "cus_01",
  "channel": {
    "type": "voice",
    "provider": "livekit"
  },
  "event": {
    "type": "customer_message",
    "content_type": "text",
    "content": "昨天的退款问题处理了吗？"
  },
  "occurred_at": "2026-09-05T01:00:00Z",
  "metadata": {}
}
```

## 3. Initial Event Types

- conversation_started
- customer_message
- agent_message
- human_message
- tool_call
- tool_result
- task_started
- task_completed
- handoff_requested
- handoff_accepted
- handoff_in_progress
- handoff_failed
- handoff_completed
- handoff_cancelled
- conversation_resolved
- conversation_reopened
- evaluation_completed

## 4. Rule

Provider Adapter 负责：

```text
Provider Event
→ Normalize
→ OpenCS Event
```

Core 不负责理解 Chatwoot Webhook schema 或 LiveKit SDK object。

## 5. 信封、版本与可信租户

Schema `$id` 标识 0.0 格式；不对既有信封新增 schema_version 字段。首次公共 API 前如改变格式，必须提供显式迁移说明。
标识符须非空，occurred_at 必须为带时区的 date-time。provider 仅为渠道来源描述，不承载业务规则。
metadata 保持扩展空间；供应商 ID 放 metadata/external reference，不能变成顶层业务字段。

tenant 从认证后的集成绑定中解析，不能信任 webhook 自报的 tenant/account。若调用者信封租户与可信绑定不一致，拒绝事件，不查询其他租户数据。
customer_id/conversation_id 必须在归一化完成前解析；无主体系统事件不在当前 0.0 信封范围。

## 6. 幂等与副作用（D5）

持久键为 `(tenant_id, event_id)`。同键、同归一化 payload 返回原接受/处理记录，不再次执行副作用；同键不同 payload 拒绝并审计。
JSON 对象键顺序不构成 payload 变化；数组顺序和内容变化构成变化。received_at/投递次数不得写入稳定事件 payload，应另存投递日志。
Adapter 的 event_id 必须能在 provider 重投时复用，不可每次生成随机新 id。

入站验证后先事务落库，再处理。入站接受不等于外部动作成功；处理中重放返回已有处理状态。失败和恢复仍关联原事件记录。
外部动作使用稳定 action_id，发送前持久记录目标、授权决策、关联事件、attempt 和预期操作；记录实际结果或 unknown。
超时结果不确定时先核对 provider 状态，不能盲重试；provider 无法查询或去重时保留 unknown，交由显式恢复操作处理。这里不承诺 exactly-once。

## 7. Handoff 事件（D2/D3）

所有 handoff_* 事件使用 content_type=application/json，content 为 [handoff snapshot](schemas/handoff.schema.json)。
event type 与 snapshot.state 一致；顶层 tenant/conversation/customer 与 snapshot 必须一致。
版本/attempt 依据 [Handoff Spec](HANDOFF_SPEC.md) 校验，不能用 occurred_at 推断有效状态顺序。
JSON Schema 验证 snapshot 结构及 event/state 映射；跨字段身份相等、持久版本、授权和幂等由语义验证与后续事务实现负责。

## 8. 验收与限制

同键重放、冲突 payload、跨租户伪造、入站后崩溃、发送成功响应丢失、旧 attempt 回调均需回归。
Foundation 验证格式与语义参考轨迹；数据库并发/重启/发送恢复留给 TASK-103/104 集成测试，不以格式通过代替。
