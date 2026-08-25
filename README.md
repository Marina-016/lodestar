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

> Lodestar 是我为 AI 产品经理岗位准备的 Agent 项目。它探索的不是“如何让模型回答更长”，而是：**Agent 如何形成有证据的判断，理解它和当前项目的关系，在用户确认后沉淀为记忆，并把洞察推进成可验证实验。**

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

### 为什么做

我上一段实习主要做 Skill 优化。在实践中我发现：Skill 能够调用成功，并不代表 Agent 真正创造了产品价值。

一个值得长期使用的 Agent，至少要回答四个问题：

1. Agent 为什么得出这个结论？
2. 这个结论和用户正在做的项目有什么关系？
3. 哪些信息应该进入长期记忆，哪些应该被拒绝？
4. 研究结果如何继续变成可执行、可评估的动作？

Lodestar 把这四个问题变成产品约束，而不是留给模型自由发挥。

### 我定义的 Agent 产品价值

    Agent 价值
    = 有证据的判断
    + 可解释的项目关联
    + 受用户控制的状态变化
    + 可以继续执行的下一步

这也是 Lodestar 和普通 Research Chat 的区别：最终答案只是界面结果，**研究轨迹、记忆变更和实验骨架才是可以复用的产品资产**。

### 四个关键产品决策

| 产品决策 | 识别到的风险 | Lodestar 的处理 |
| --- | --- | --- |
| 默认稳定回放 | 模型、网络或 API 失败会破坏产品演示 | 线上默认 Curated Demo Replay，本地保留真实研究模式 |
| 记忆经过确认 | Agent 自动写入会污染后续上下文 | 先生成 Knowledge Delta，再由用户选择是否记住 |
| 关联度可解释 | 单一相似度分数无法支持产品决策 | 拆分技术栈、项目语境、代码证据、进行中状态 |
| 轨迹是一等产物 | 只展示最终答案，无法建立信任 | 保留来源、工具调用、代码匹配和状态变化 |

### 目标用户

需要持续跟踪 AI 进展、同时推进具体项目的产品经理、研究者和工程负责人。

典型任务包括：

- 每周扫描 Agent、Memory、Tool-use 的新进展；
- 判断一篇论文是否值得进入当前项目的验证队列；
- 复盘一次 Agent 研究过程，而不是只看最后一句答案；
- 将研究洞察变成 baseline、candidate、eval 三件套；
- 在多次对话之间保留经过确认的知识。

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

## 作为 AIPM，这个项目体现了什么

### 从功能清单转向用户任务

没有从“我有 Memory、Tool Calling、SSE”开始设计，而是先定义使用者可以理解的任务：

> 发现变化 → 判断相关性 → 查看证据 → 控制记忆 → 生成验证动作

### 给 Agent 的自主性设边界

Agent 可以自主搜索、整理和提出建议，但不能静默修改长期记忆。这是对“错误记忆会如何影响后续任务”的风险判断。

### 把关联度做成可讨论的决策依据

项目关联不是一句“这很相关”，而是拆成技术栈命中、项目语境命中、代码证据和进行中状态。这样产品经理可以继续追问：哪个维度贡献最高？怎样调整权重？怎样设计评估集？

### 把研究结果推进到下一步

每个重要判断都必须继续回答：

> 应该更新什么知识？
> 应该验证哪个假设？
> baseline 和 candidate 如何比较？
> 成功标准是什么？

### 对 Demo 稳定性负责

产品 Demo 的目标不是证明“模型偶尔能回答”，而是让使用者稳定看到完整产品链路。因此线上默认回放、本地保留真实模式，是对可靠性和真实能力之间的主动取舍。

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
