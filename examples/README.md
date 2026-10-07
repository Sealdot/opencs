# Reference examples

The approved B2B SaaS billing policy is retained in `saas-billing/policy.json`.
The independently packaged implementation and usage guide are in
[`opencs/examples`](../opencs/examples/README.md).

Run `python -m opencs.examples.billing` to exercise invoice lookup, enforced refund
restrictions, structured handoff and pending human approval with synthetic HTTP.
Example-specific refund rules and queue names remain explicit configuration.
