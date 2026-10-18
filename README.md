# Lodestar · Agent Research Lab

面向 AI 从业者的个人论文研究工作台：发现论文、连续讲解、维护技术与方法的学习证据，并结合项目形成技术方案与实验草案。

当前版本：**V0 / 0.5.0**。主入口是极简白色本地 Web 工作空间。模型自主理解问题和选择工具，宿主负责数据范围、运行状态、来源记录和费用边界。

## 现在能做什么

- **主对话**：持续交流，用自然语言委派多个研究任务。
- **任务**：各自保存聊天、来源、运行进度和模型设置；可以同时推进、单独停止、完成和归档。
- **共享记忆**：同一个用户身份，个人偏好跨任务共享；项目事实仅在绑定项目内使用；任务备注保持局部。
- **记忆管理**：查看、搜索、编辑、停用、恢复、遗忘；查看学习证据、纠正自评或撤销证据；Web聊天可按自然语言请求操作。
- **论文研究**：arXiv 检索、HF 热门信号、论文阅读、连续追问，以及已存在的项目适用性与实验后端。
- **模型设置**：同服务模型目录与随模型变化的推理选项；运行开始时冻结配置。

可以输入：

> 分别建两个任务，研究 Harness 和 RAG 的近期论文。结合当前项目给出值得验证的方法，先不要改代码。

后台任务共享相关记忆，但不会自动读取其他任务的全部对话。主对话可按需读取任务结果。**后台读过论文，不代表用户接触或掌握了论文方法。** 学习围绕技术、知识点和方法记录，论文只作为关联来源；概念定义由模型组织，不保存成个人学习事实。

## 快速开始

Python 3.10+，在仓库根目录执行：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m lodestar ui --port 8124 --no-browser
~~~

打开 http://127.0.0.1:8124/ 。在本地 .env 配置服务地址、模型与密钥。示例显式暂停真实模型调用；需要离线体验时设置 LODESTAR_LLM_MODE=mock、LODESTAR_SEARCH_MODE=mock。真实调用前核实账户与所选模型的免费额度，首页加载和刷新不会调用模型。

其他系统激活虚拟环境后使用 python -m lodestar ui。旧橙色回放入口仍可使用 python -m lodestar ui --legacy；旧代码完整保留在 [codex/legacy-orange-v0](https://github.com/Marina-016/lodestar/tree/codex/legacy-orange-v0)。[旧线上演示](https://lodestar-beige.vercel.app) 是固定回放，不是新版真实后端。

## 简洁的 Harness

~~~text
用户消息
   ↓
当前任务的近期对话 + 有界共享记忆 + 项目范围
   ↓
模型自主决策 ←→ 搜论文 / 读论文 / 读网页 / 查任务 / 管记忆
   │                  └─ 主对话可派发子任务，各自运行
   ↓
流式回答 + 引用来源 + 运行记录
~~~

没有固定对话阶段或关键词意图分类器。只有必要的运行生命周期、操作预算、取消与数据范围检查。论文分析、项目方案和实际代码实验使用独立接口；网页聊天不会自动执行代码或采用方案。

相关实现：lodestar/agent/dialogue.py、lodestar/web/server.py、lodestar/web/tools.py、lodestar/web/memory.py。

## 数据和边界

Web 数据保存在被 Git 忽略的 workspace/web/web.db，与 CLI 研究数据库分开。旧 Web 任务会保留会话和消息，迁移为共享用户身份；旧任务备注仍是任务范围，不会自动变成个人事实。升级前应通过 SQLite backup API 备份数据库。

修改、停用或遗忘记忆，会重新建立受影响任务的后续上下文边界，避免旧回答重新带回失效事实。这会暂时减少模型可用的历史上下文；用户可见聊天保留。遗忘会清理该记忆的修订正文，但不会撤回云端已收到的信息，也不清理备份或聊天中原本的文字。

当前服务仅监听 127.0.0.1，针对本地单用户。**本版不是已验收的公网多用户服务。** 公网发布仍需认证、用户数据隔离、HTTPS、持久化备份、限流与真实模型/搜索验收。现有 Vercel 配置服务于旧回放，不应直接作为新版后端部署方案。

公共网页搜索可能被引擎验证拦截，错误会明确返回。阿里官方联网搜索 MCP 已有可选适配与本地免费次数预约机制；必须核实并登记剩余额度后启用，无额度不自动付费回退。模型调用开关及本地预算本身不保证云端零费用。

项目登记和绑定只建立名称、描述及范围，不代表读取或外发整仓库的授权。项目代码外发须匹配明确文件白名单。密钥、数据库、缓存和真实运行日志不进入 Git。

## 验证与文档

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/web_sources.test.mjs tests/web_actions.test.mjs
node --check lodestar/web/static/app.mjs
.\.venv\Scripts\python.exe scripts/preflight_public_release.py
~~~

master 基线的真实证据与未通过项见 [10-15 续检](docs/v0-live-review-20261015.md)，历史功能验收见 [V0 发布验收](docs/v0-release-check.md)。质量分支的最新机制验证与真实检查见 [10-18 记录](docs/v0-quality-review-20261018.md)，此前失败证据见 [10-17 记录](docs/v0-quality-review-20261017.md) 与 [语义审阅](docs/v0-quality-semantic-review-20261017.md)。绿色功能测试不代表模型语义或服务可靠性已全部通过。

- [产品逻辑与系统架构](docs/agent-prd.md)
- [Web 使用与数据说明](docs/web-ui.md)
- [后端运行手册](docs/agent-mvp-runbook.md)
- [对话用法](docs/chat-usage.md)
- [阿里官方搜索接入](docs/aliyun-web-search.md)
- [Changelog](CHANGELOG.md)

## V0 质量版开发

2026-10-17 起在 `codex/v0-stability-20261017` 推进质量与可靠性，不改变 master 的发布边界。执行顺序与放行条件见 [开发计划](docs/v0-quality-plan-20261017.md)，人工语义检查见 [八组验收卡](docs/v0-quality-cases.md)，最新验证与剩余边界见 [10-18 验收](docs/v0-quality-review-20261018.md)。

本轮移除模型服务的隐式重试/参数降级，修复线程启动失败占住任务槽位和取消竞态；mock/DashScope 不再加载无关 Anthropic SDK。Windows 实验凭证不查询 WMI，但旧系统卡点的根因仍未查明。

固定合成证据的隔离 HTTP 验收：

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.eval_workspace --scenario quality
~~~

默认不调用真实模型，只验证管道。QF-1 是明确虚构的验收材料，快照、哈希与人工事实表分别保存；参考答案不会注入模型，不自动给研究语义打通过分。真实验收支持 Anthropic 和 DashScope 的整轮物理请求上限；这个上限不是云端免费额度总账。旧 24 次授权账本保持耗尽；追加免费验收使用独立账本、每轮请求上限及 DashScope 本地 token 预约，并保留余量。双任务在宿主续跑后可读取终稿、保存并纠正偏好；回答过长和未受证据支持的推断仍未放行。固定证据中，换用 Max 或精简提示词都未解决模型只读摘要便断言无实验的问题，因此不把模型升级或非空回答视为质量通过。学习纠正兼容合法冗余参数，记忆搜索同时披露过滤范围；有种子的召回与遗忘通过，完整连续语义仍待复核。最新 450 项 Python 与 14 项前端通过；此前本机 HTTP 间歇性失败及复核边界见 [公开材料续检](docs/v0-public-evidence-review-20261018.md)。正文读取已补齐编号章节和 Approach/实验片段，修复通用 PDF 查询与同名缓存问题；验证范围见 [PDF 读取续检](docs/v0-pdf-evidence-review-20261018.md)。默认调用开关、模型和 .env 均未修改。

公开论文的冻结材料可通过 `--scenario public-quality --source-packet <冻结JSON路径>` 经同一隔离 HTTP 路径验收；它保留真实来源版本，正文按需交付，仍需独立人工审阅。准备方法与范围见 [公开材料续检](docs/v0-public-evidence-review-20261018.md)。报告区分实际正文与摘要回退，不以 full_text 请求参数代替阅读证据。

学习召回已修复大小写重复和超过500方法后的较早匹配遗漏，读取结果披露匹配数量与截断范围。五轮脚本覆盖过滤无匹配后概览读取、纠正与遗忘；真实语义仍待复核，详见 [记忆召回续检](docs/v0-memory-recall-review-20261018.md)。

## 后续重点

真实模型与论文质量回归、可用的通用搜索、长期历史检索、后台服务与通知，以及带认证的公开服务。当前不自动采用技术方案，不自动判定用户掌握，不宣称固定样本实验等于论文复现。

Apache-2.0，详见 [LICENSE](LICENSE)。
