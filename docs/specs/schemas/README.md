# JSON Schema 0.0 基线

2026-09-07：根据维护者采纳的 D1–D6 定稿。格式编号 0.0 尚未发布为稳定公共 API。
标准为 [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-core)。

| 文件 | 覆盖 | 外部语义校验 |
|---|---|---|
| event.schema.json | 原信封、17种事件、handoff event/state 对应 | 可信租户、snapshot身份相等、幂等、持久版本 |
| handoff.schema.json | 责任转移上下文、状态、attempt、version、人工动作证据引用 | 合法转换、单活动hand-off、回调证据真伪 |
| scenario.schema.json | given/user/expect、工具集合、handoff/reason、resolution | required/forbidden不能重叠、工具实际执行证据 |
| evaluation-result.schema.json | 六指标、逐项状态/解释、pass/fail证据引用 | 引用可解析性、真实结果、所有确定性断言必须pass |

`uv run python scripts/check_foundation.py --contracts-only` 使用本地 registry 解析 Schema，不获取网络引用。
公开源目录不附原 kit；完整私人工作区省略该参数时还会校验 36 个 kit 原件。
`validate_contract` 补充信封/交接主体相等及工具集合不冲突；`check_handoff_trace.py` 是合成轨迹的规格参考校验，不能作为生产存储或授权实现。

与 2026-09-05 候选格式相比：新增 handoff schema、handoff_in_progress/failed 两事件、事件/状态约束、结果证据要求；账单 reason 统一。
保留原信封字段，Schema通过 `$id` 标识版本。仓库无已发布API或持久数据，不需要运行迁移；后续破坏性格式变更必须写兼容说明。

标识必填，时间带时区；metadata开放；channel不枚举供应商。非handoff负载仍为文本/object/null；各类tool/system payload细化随用例推进。
格式验证不能证明权限、数据库并发、真实调用或客服解决效果。result fixture 的六项均为 not_evaluated。
