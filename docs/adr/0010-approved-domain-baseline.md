# ADR-0010: D1–D6 领域基线

Status: Accepted

Date: 2026-09-07

## Context

Foundation 审查发现会话边界、人工责任、重试、账单权限、事件可靠性及评估断言尚未确定。
维护者已在当前任务中对 D1–D6 推荐方案回复「采纳」。

## Decision

采用 [完整决策记录](../bootstrap/DECISIONS.md) 中六项推荐：

- D1：租户内显式 provider conversation/contact 映射；关闭重开复用 Core Conversation，不自动跨渠道合并身份。
- D2：Requested 起禁发新的 AI 客户输出；assignment 不是 Accepted；失败取消不自动恢复；单活动 handoff 与版本校验。
- D3：原状态图不变，增加 handoff_in_progress/failed；同 handoff id 重试、新 attempt/event id；保留历史并拒绝旧回调。
- D4：账单示例只允许客户/账单查询，禁止退款调用，reason 为 refund_requires_approval，pending_human 不算解决。
- D5：可信集成绑定租户；持久幂等、冲突拒绝、入站先落库；出站 action id 与先审计，结果不确定时先核对。
- D6：required 工具成功、forbidden 无调用尝试、有效 handoff request/reason；确定性断言全过，六指标分别记录，不合总分。

## Alternatives

原 D1–D6 表保留替代方案和代价，未选择的行为不进入当前规格。

## Consequences

可完成 Foundation 的领域语义确认；后续实现不需重复询问上述决策。
Accepted 阶段取消/失败、自动恢复 AI、时间窗切分/身份合并仍未支持，不擅加状态边。
格式编号 0.0 是本地规格基线，不是稳定公共 API 或已发布版本。

## Maintainer Impact

规格变更与对应回归统一维护；用户已批准的语义不反复请求确认。

## Open Source Impact

保留不可变 kit 和可追溯批准记录；未批准的其他 ADR 维持原状态。
