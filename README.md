# Lodestar · Agent Research Lab

<p align="center">
  <strong>一个把 AI 前沿研究，转化为项目决策与可验证实验的 Agent 产品原型。</strong>
</p>

<p align="center">
  <a href="https://lodestar-beige.vercel.app">在线 Demo</a> ·
  <a href="https://github.com/Marina-016/lodestar">GitHub</a> ·
  <a href="#产品定位">产品定位</a> ·
  <a href="#产品思考">产品思考</a> ·
  <a href="#技术架构">技术架构</a>
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Demo replay" src="https://img.shields.io/badge/Demo-Curated%20Replay-F28C28">
  <img alt="Tests" src="https://img.shields.io/badge/Tests-12%20offline%20checks-2EA44F">
  <img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-4A5568">
</p>

> Lodestar 面向 AI 从业者，用于探索和落地最新 Agent 技术。它探索的不是“如何让模型回答更长”，而是：**Agent 如何形成有证据的判断，理解它和当前项目的关系，在用户确认后沉淀为记忆，并把洞察推进成可验证实验。**

## 产品定位

**Lodestar 是一个项目感知、证据可追溯、记忆受治理的研究 Agent。**

它把“本周 AI 有什么新进展”转化为一条完整的产品链路：

    研究问题
       ↓
    扫描前沿信号，拆解研究任务
       ↓
    检索论文、PDF、Web，并记录工具调用
       ↓
    计算项目关联度，定位代码证据与实现缺口
       ↓
    生成 Research Brief 与 Knowledge Delta
       ↓
    用户确认记忆更新
       ↓
    生成实验假设与代码骨架

在线 Demo 默认使用 Curated Demo Replay：无需 Token、无需联网、输出稳定，适合稳定展示和录制产品演示。
打开 [lodestar-beige.vercel.app](https://lodestar-beige.vercel.app)，直接发送预置问题即可体验。

## 产品思考

### 核心产品命题：Agent 的价值不是回答，而是完成一次可信的状态转移

传统 Research Chat 的终点是生成一段文字。Lodestar 把终点向后推进：

    外部信息
       ↓
    可解释判断
       ↓
    项目决策
       ↓
    用户确认的长期记忆
       ↓
    可执行、可评估的实验

因此，一个 Agent 是否有价值，不只看回答是否流畅，还要看它是否让使用者更快、更有依据地做出下一步决策。

### 面向 AI 从业者的使用场景

Lodestar 面向需要持续吸收最新 AI 技术、并将其转化为实际产品或工程动作的从业者：

- 研究 Agent、Memory、Tool-use 等方向的最新论文和实践；
- 判断一个新技术是否与当前产品或代码库真正相关；
- 将“值得关注”转化为“值得验证”的具体假设；
- 在多次任务之间保留经过确认的知识，而不是堆积未经筛选的上下文；
- 用统一的 Trace、来源和评估指标复盘 Agent 行为。

### 我做出的关键产品判断

| 产品命题 | 关键风险 | 设计回应 |
| --- | --- | --- |
| 新颖性不等于优先级 | 热点很多，但大多数无法落到当前项目 | 用项目语境、技术栈、代码证据和进行中状态计算关联度 |
| 检索不等于记忆 | 把所有检索结果写入上下文会放大噪声和错误 | 将记忆设计成需要用户确认的 Knowledge Delta |
| 自主不等于无限授权 | Agent 可以执行动作，不代表可以修改长期状态 | 将搜索、判断、写入、实验生成拆成不同权限层 |
| 结论不等于可信 | 没有来源和轨迹，使用者无法复核 | 将 Evidence Trace 做成产品界面，而不是后台日志 |
| Demo 不等于真实产品 | 过度依赖实时模型会让展示不可重复 | 线上使用 Curated Replay，本地保留真实 Research Loop |

### Agent 的自主边界

Agent 可以自主完成：

- 研究任务拆解；
- 来源检索与去重；
- 论文和项目证据整理；
- 形成研究判断和实验建议。

Agent 不能静默完成：

- 修改长期 Knowledge State；
- 将不确定内容伪装成确定结论；
- 用单一相似度分数替代项目判断；
- 在没有成功标准的情况下生成“看起来合理”的实验。

这条边界的本质是：**让 Agent 自主处理信息，让使用者控制不可逆的状态变化。**

### 评估不只看答案质量

Lodestar 将评估拆成多个产品指标，而不是只做最终文本打分：

| 指标方向 | 关注问题 |
| --- | --- |
| 证据覆盖率 | 结论是否都有可追溯来源 |
| 来源独立性 | 多个来源是否真的提供了独立证据 |
| 项目关联一致性 | 人与 Agent 对“是否值得验证”的判断是否一致 |
| 记忆精确率 | 写入的内容有多少是真正值得长期保留的 |
| 记忆风险率 | 错误、冲突或过时信息是否进入后续上下文 |
| 实验转化率 | 研究洞察能否形成有成功标准的实验 |
| 资源成本 | Token、工具调用次数和端到端延迟是否可接受 |

### 产品演进的优先级

当前优先保证研究链路的可信和可复盘，再逐步增加更强的实时能力：

1. 证据和项目关联可解释；
2. 记忆写入可控制；
3. 研究结果可以进入实验；
4. 用离线评估验证行为；
5. 再扩展实时检索、多项目记忆和成本优化。

这是一种“先建立信任，再扩大自主性”的 Agent 产品策略。

## Demo 里能看到什么

### 推荐演示路径

1. 输入：“本周 Agent 研究有哪些值得 Lodestar 优先验证的新进展？”
2. Agent 输出三个信号：概念、论文发现、与 Lodestar 的具体关联；
3. 查看原始论文标题和 PDF；
4. 展开项目关联度、命中的代码文件和实现缺口；
5. 确认 Knowledge State 更新；
6. 查看关联代码；
7. 生成实验骨架，进入实验项目页查看 baseline.py、candidate.py、eval.py。

### 一个 Research Brief 必须回答

    发生了什么？
    论文具体证明或观察到了什么？
    它和当前项目的关联在哪里？
    证据质量和不确定性如何？
    哪些内容值得记忆？
    下一步怎样用一个小实验验证？
## 技术实现与能力地图

| 能力 | 产品层表现 | 对应实现 |
| --- | --- | --- |
| 研究规划 | 把一个开放问题拆成前沿扫描、证据展开和项目判断 | lodestar/agent、lodestar/frontier.py |
| 工具调用 | 按任务选择搜索、论文、项目索引和知识工具 | lodestar/tools、lodestar/mcp_server.py |
| 证据追踪 | 每个结论都能回到来源和读取深度 | lodestar/trace、research_tasks.trace_events |
| 项目关联 | 解释关联度分数，并展示命中的代码文件 | lodestar/relevance.py、lodestar/project_index.py |
| 记忆治理 | 先提出 Knowledge Delta，再由使用者确认是否写入 | lodestar/memory/repo.py |
| 流式反馈 | 研究过程逐步反馈，而不是等待黑盒答案 | lodestar/ui.py、/api/task/:id/stream |
| 离线评估 | 检查来源覆盖、忠实性、任务成功和记忆行为 | tests、lodestar/eval |
| 实验闭环 | 将研究判断转成可执行的实验项目 | lodestar/experiment.py、workspace/experiments |

## 工作方式

每次研究都同时产生一条对话和一组可复用状态：

    Conversation
      ├── Research Task
      │     ├── Plan
      │     ├── Sources
      │     ├── Trace Events
      │     ├── Project Relevance
      │     └── Research Brief
      ├── Knowledge Delta
      └── Experiment Opportunity

研究结果不会停留在聊天气泡中。使用者可以回到来源、检查证据、确认记忆，并继续生成实验。

## 技术架构

    使用者问题
       ↓
    Lodestar UI
       ↓
    HTTP API / SSE
       ↓
    研究编排器
       ├── 任务拆解
       ├── Tool Registry
       ├── 论文 / PDF / Web
       ├── 项目索引
       ├── Knowledge State
       ├── Research Trace
       └── Research Brief
                  ↓
             使用者确认
               ├── 记忆更新
               └── 实验骨架 → 离线评估

## 快速运行

环境要求：Python 3.10+；Windows、macOS 或 Linux；体验演示回放时不需要 Token。

    python -m venv .venv
    python -m pip install -r requirements.txt
    $env:LODESTAR_DEMO_REPLAY="true"
    python -m lodestar demo reset
    python -m lodestar ui --port 8123 --no-browser

打开 http://127.0.0.1:8123。

## Vercel 部署

线上版本强制使用演示回放，并将临时状态写入 /tmp。Vercel 是稳定的展示面，本地模式是开发与真实研究面。

    npm install -g vercel
    vercel login
    vercel link
    vercel --prod

## 评估与路线图

当前离线检查覆盖来源唯一性、证据覆盖、论文元信息、研究结论忠实性、Project Relevance、Knowledge State 确认流程和 Experiment Scaffold 生成。

下一步计划：Claim-level citation diff、Memory Trap Rate、False-majority Rate、Token Cost、多项目记忆域和可视化评估面板。

## 安全与公开仓库策略

公开前运行：

    python scripts/preflight_public_release.py

不会提交 .env、API Key、Access Token、Cookie、Private Key、SQLite 数据库、PDF 缓存和机器特定路径。

真实 Provider 凭据只放在本地环境或 Vercel Environment Variables 中，绝不写入代码。

## 许可证

Apache-2.0，详见 LICENSE。

---

<p align="center">
  <strong>把问题带到证据，把理解留在记忆里，把洞察推进成实验。</strong>
</p>
