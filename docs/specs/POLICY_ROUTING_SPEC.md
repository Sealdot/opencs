# Policy and Routing Specification

Baseline supplement: 2026-09-07，D4 已采纳。下文金额阈值与企业故障路由均为未来规则示例，不能作为 V0.1 Billing 的执行默认。

## 1. Policy ≠ Prompt

错误：

```text
System prompt:
"如果退款超过 100 元，请转人工。"
```

正确：

```yaml
refund:
  allowed:
    - get_invoice
    - compare_invoice
  approvals:
    refund:
      ai_auto_max: 20
      human_required_above: 20
  forbidden:
    - modify_contract
```

## 2. Routing Inputs

V0.4 前后逐步支持：

- intent
- risk
- customer / organization value
- complexity
- AI confidence
- tool result
- SLA risk
- agent availability
- channel constraints

## 3. Initial Routing Example

```yaml
routes:
  - name: enterprise_outage
    when:
      organization.plan: enterprise
      intent: service_outage
      impact: organization_wide
    target:
      type: human_queue
      id: p1_support
    priority: critical
```

## 4. Design Rule

第一版只做：

**Rules + AI Classification**

先保证可解释、可测试、可审计，再考虑模型化 Routing。

## 5. V0.1 Billing 最小规则（D4）

配置位于 [examples/saas-billing/policy.json](../../examples/saas-billing/policy.json)。
允许 get_customer/get_invoice；禁止 issue_refund。重复扣费调查后的所有退款一律人工审批，reason=refund_requires_approval，结果 pending_human。
未列入 allowlist 的工具不得执行。工具调用前检查确定性 policy 和租户权限，并保留拒绝/允许的审计；不能只靠 prompt 禁止退款。
外部人工退款流程不由本例调用或实现。上文 ai_auto_max=20 的示例不会覆盖本例规则。
完整路由引擎、动态队列选择和企业风险分级仍属于后续版本。
