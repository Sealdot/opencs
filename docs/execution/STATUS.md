# OpenCS 0.1.0a1 离线预览状态

## Python 3.13 验证补记 — 2026-10-12

[PR #4](https://github.com/Sealdot/opencs/pull/4) 已合并，CI 在 Python 3.12 和 3.13 上分别运行完整检查，显式选择并断言实际解释器版本，分别保存构建与安装后验收产物。
[PR CI](https://github.com/Sealdot/opencs/actions/runs/38225304499) 的两个版本均为 601 项测试通过；格式、lint、类型、契约、wheel/sdist 安装、八个预期场景结果与 sdist 重建一致性检查均通过。
本地 macOS ARM64 的 Python 3.12.13 / 3.13.12 也通过同一套检查。

0.1.0a1 是此项验证补齐的独立 GitHub 预发布版本。版本元数据与安装示例同步更新，运行时 API、领域规则和第三方依赖版本保持原样。
发布前从公开仓库的已提交文件重新导出逐文件清单与冻结哈希；发布门槛为标签对应提交的 push CI 与手动冻结快照 CI，两者的实际链接、源码和包校验和见 [v0.1.0a1 Release](https://github.com/Sealdot/opencs/releases/tag/v0.1.0a1)。
v0.1.0a0 的标签与附件保留。Python 3.14+ 和真实服务仍未验证，PyPI 不在本次发布范围内。

## 公开状态补记 — 2026-10-08

[公开仓库](https://github.com/Sealdot/opencs)与 [v0.1.0a0 GitHub prerelease](https://github.com/Sealdot/opencs/releases/tag/v0.1.0a0)已于 2026-10-07 发布。
标签对应公开提交 `be9a2c2a9da389fb409e78a469083ee5f85abec2`；该提交的 [push CI](https://github.com/Sealdot/opencs/actions/runs/37622160952)与[手动冻结快照 CI](https://github.com/Sealdot/opencs/actions/runs/37622178303)均已通过。
[许可与权属核对](../release/LICENSE_AUDIT.md)已记录维护者发布授权及有界 kit 来源追溯，并保留证据限制；该记录不构成独立权属证明或法律意见。
本次发布仍仅覆盖既有 IM Runtime 的离线开发预览，PyPI 不在范围内；下文生产与真实集成验收限制继续适用。

## 发布前候选快照 — 2026-10-07

以下保留发布准备阶段的原始记录；其中“托管 CI 尚未运行”和“剩余公开前工作”等描述仅适用于当时。当前公开状态以上方补记及链接证据为准。

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
