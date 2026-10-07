# Packaged billing example

Run with the existing Python environment; no server, API key, network, database,
model download or additional dependency is required:

```sh
UV_OFFLINE=1 uv run --no-sync python -m opencs.examples.billing
UV_OFFLINE=1 uv run --no-sync python -m opencs.examples.billing --fault forbidden-tool
UV_OFFLINE=1 uv run --no-sync python -m opencs.examples.billing --fault tool-failure
UV_OFFLINE=1 uv run --no-sync python -m opencs.examples.billing --fault send-timeout
```

`--output path.json` writes a detailed audit/evaluation report. Normal execution exits
0; injected faults intentionally exit 1. Each report checks replay without extra
actions and declares `provider_calls=0`, `model_inference=false`, and `live_not_run`.
Example JSON is packaged as resources so the wheel works outside the repository.

The execution path is Chatwoot normalization → Runtime → structured OpenAI response
mapping → Generic HTTP queries → reply → private context note → assignment. All HTTP
responses and decisions are scripted through `FakeTransport`. This tests the actual
formal interfaces and orchestration, including the policy denying `issue_refund`.
It does not measure a model's ability to choose those actions.

`pending_human` is the expected business result, with handoff state `requested`.
The evaluator separately records deterministic assertions and six quality metrics.
Policy/tool/handoff metrics cover only documented offline checks. Resolution,
correctness and grounding remain `not_evaluated`; there is no aggregate quality score.

Historical Voice text simulations remain in the private workspace and are excluded
from this public candidate. This IM package does not claim a LiveKit SDK, STT/TTS,
WebRTC/SIP or audio-interruption implementation. Voice expansion is paused.
