# OpenCS 0.1.0a0 离线预览候选 — 2026-10-07

本轮只收口既有 IM Runtime，没有新增架构、Voice、生产数据库或供应商。
Foundation D1–D6 已批准；原离线任务与后续 HTTP 契约实现保留。
完整历史、kit 与上游贡献记录留在私人工作区。

候选源码从原始分支 `codex/upstream-webhook-contributions` 的
`b0a0d72` 创建独立 worktree；原工作区开始时无未提交改动，未配置 OpenCS 远端。
本轮修改位于独立 `codex/offline-release-candidate` 分支。
最终来源提交与逐文件哈希以导出目录 `SOURCE_BASELINE.json` 为准，
新公开基线应另建无历史的本地 Git root commit，不能推送私人分支历史。

公开候选包含正式 runtime、已有 adapters、必要规格/决策、合成 examples、
正式测试、CI、构建/验收脚本与发布草稿。逐文件清单见
[public-files.json](../release/public-files.json)。原 kit、根编号输入、experiments、
第三方 checkout、上游贡献记录、凭据、个人路径、缓存和旧 dist 均不导出。

验收入口：格式/lint/类型/测试/合同检查、公开目录清单及哈希检查、
两个新 venv 从 wheel 与 sdist 安装、八个预期场景结果、sdist 重建一致 wheel。
实际计数、来源提交、包哈希及执行输出记录在独立候选 evidence 目录，
不把运行产物混入源码。CI 已准备；托管 CI 尚未运行。

剩余公开前工作：确认 [权属疑点](../release/LICENSE_AUDIT.md)，设置安全报告与社区私联渠道，
再按 [PUBLISHING](../release/PUBLISHING.md)审阅独立基线并发布候选。
本轮不创建远端、不发布 Release 或 PyPI。技术候选不等于许可已清理完毕。

生产存储、跨进程幂等/崩溃恢复、真实模型质量、供应商鉴权与人工接单、
LiveKit 音频、部署与生产安全均未验收（`live_not_run`）。内存状态重启即丢失。
正常账单结果 `pending_human` 仅是待人工处理，不是人工已接单或问题解决。
