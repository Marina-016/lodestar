# Lodestar 可信记忆 Demo 录制脚本

## 一句话故事

Lodestar 不只扫描本周论文。它会结合用户已有的 Knowledge State 和当前项目代码，识别真正相关的技术缺口，经用户确认后更新长期记忆，并把洞察转成可验证实验。

本次主线不是“让 Agent 记得更多”，而是：

> 可信记忆：Agent 如何判断一条记忆该不该被使用？

## 录制前的数据状态

执行 demo reset 后，工作区应只包含：

- 1 个项目：Lodestar / Agent Research Lab
- 8 个基线 Knowledge State 概念
- 4 条经过整理的研究记录
- 13 条论文或项目证据
- 4 个实验，其中 2 个是 scaffolded、2 个是 draft
- 0 条对话、0 条待确认记忆

基线中故意不包含 Memory Trust Gate。用户确认后，它才会成为第 9 个概念。

## 本周论文依据

- [MemTrapBench](https://arxiv.org/abs/2608.20202)：真实且语义相关的记忆也可能造成 Reasoning Fixation 和 Belief Distortion。
- [Beyond Memory Majority](https://arxiv.org/abs/2608.19701)：多条记忆可能继承同一上游来源，形成虚假多数。
- [Break It Down, Pass It On](https://arxiv.org/abs/2608.20274)：子任务级文本 Skill 比完整任务级或代码 Skill 更容易跨任务迁移。
- [Optimal Skill Selection](https://arxiv.org/abs/2608.19993)：Skill 应在 Token 预算下按组合收益选择，而不是逐条 Top-K。
- [MidTool](https://arxiv.org/abs/2608.20314)：通用 Tool Calling 开始进入专门的中期训练数据流程。

以上论文均在 2026 年 8 月 20 日提交，并进入 arXiv 8 月 21 日公开批次。视频中应说“本周最新批次中的重点方向之一”，不要声称它是客观热度第一。

## 完整工作流

~~~mermaid
flowchart LR
    A[本周论文扫描] --> B[结合 Knowledge State 去重]
    B --> C[读取 Lodestar 项目索引]
    C --> D[选择可信记忆方向]
    D --> E[展开论文证据]
    E --> F[定位现有代码缺口]
    F --> G[展示可审计 Agent Trace]
    G --> H[用户确认 Memory Trust Gate]
    H --> I[Knowledge State 更新]
    I --> J[生成可信记忆对照实验骨架]
~~~

## 90–105 秒录制脚本

### 0–8 秒：建立项目语境

画面：

1. 打开“我的项目”。
2. 停留在 Lodestar / Agent Research Lab 项目卡片。
3. 快速展示技术栈和项目说明，然后回到“研究对话”。

旁白：

> 这是我做的 AI 研究 Agent。它不仅保存用户知识，也会索引当前项目，让新论文最终回答：这项技术对我正在做的产品有什么用。

### 8–22 秒：扫描本周最新方向

在输入框发送：

> 本周 Agent 研究有哪些值得 Lodestar 优先验证的新进展？

预期结果：

1. 可信记忆
2. Skill 迁移
3. 上下文预算

旁白：

> Lodestar 先读取最新论文，再结合我的 Knowledge State 和项目代码排序。这里它没有把一篇论文直接当成一个热点，而是把多篇论文聚合成产品问题。

### 22–34 秒：解释为什么选择可信记忆

画面：

1. 停留在“项目关联”。
2. 展示 Lodestar 项目名称、观察到的缺口，以及命中的 frontier.py、relevance.py、project_index.py。
3. 展开 Agent Trajectory。

旁白：

> 它把可信记忆排在第一，不是因为这个词最热门，而是因为 Lodestar 已经有记忆确认和重审，却缺少“召回之后、注入上下文之前”的可信判断。

### 34–56 秒：展开论文证据与代码关联

继续发送：

> 展开第 1 条：可信记忆。结合最新论文和 Lodestar 现有代码，告诉我为什么相关记忆也可能伤害推理，以及应该怎么验证。

画面依次展示：

1. 一句话结论。
2. MemTrapBench 和 Beyond Memory Majority 两组来源。
3. “项目关联”中命中的 memory/repo.py、agent/loop.py、trace/recorder.py。
4. Project Opportunities 中的 Memory Trust Gate。

旁白：

> MemTrapBench 发现，记忆即使真实、相关，也可能让模型形成推理固化。另一篇论文指出，多条 Agent 记忆可能来自同一个上游来源，却被错误地当成多数意见。

> Lodestar 因此定位到自己的具体缺口：当前记忆可以写入、确认和重审，但召回结果还没有经过相关性、来源独立性、冲突风险和时效性的联合判断。

### 56–68 秒：展示可审计轨迹

展开 Agent Trajectory，停留在以下步骤：

- 加载整理来源
- 关联项目代码
- 评估记忆风险
- 提出记忆更新
- 等待用户确认

旁白：

> 这些判断不是藏在一段回答里。工具调用、论文证据、代码命中和记忆风险评估都进入同一条 Trace，可以回看 Agent 为什么得出这个结论。

### 68–80 秒：用户确认长期记忆

画面：

1. 点击“记住选中的结论”。
2. 打开“你的认知地图”。
3. 搜索或定位 Memory Trust Gate。

旁白：

> Agent 不会静默改写长期记忆。只有我确认后，Memory Trust Gate 才从研究结论进入 Knowledge State，并保留证据和确认轨迹。

### 80–96 秒：把洞察转成实验骨架

画面：

1. 打开“从洞察到实验”。
2. 找到“Memory Trust Gate 能否在不降低正常任务正确率的前提下，减少 memory trap 与 false majority？”
3. 点击“生成骨架”。
4. 停留在 scaffolded 状态。

旁白：

> 最后，Lodestar 不直接宣布这个方案有效，而是生成对照实验。Baseline 是直接注入语义 Top-K，Candidate 是先经过 Trust Gate；两组固定模型、工具权限和上下文预算。

> 实验覆盖误导性相关记忆、同源虚假多数、过期冲突和正常有用记忆，同时观察任务正确率、memory safety 与 Token 成本。

### 96–105 秒：收束

画面回到研究结果或项目卡片。

旁白：

> 这就是 Lodestar 的完整闭环：发现新技术，理解证据，关联当前项目，经用户确认更新记忆，再把洞察变成可验证的产品实验。

## 录制时必须保持的真实性

- 页面会显示“演示模式 · 预置研究回放”，不要将其描述成实时联网生成。
- 论文、日期和摘要结论来自真实 arXiv 页面。
- 项目关联来自本地代码索引，不是预先画好的静态截图。
- Memory Trust Gate 是研究提出的候选能力；实验未通过前，不说已经提升效果。
- 点击“生成骨架”只代表形成可执行研究协议，不代表实验已经完成。

## 失败时的快速恢复

1. 关闭当前服务。
2. 重新执行 python -m lodestar demo reset。
3. 重新启动 8123 端口。
4. 刷新页面后确认对话为空、Knowledge State 为 8 条。

每次 reset 都会先将当前数据库备份到 workspace/demo_backups；该目录不会上传 GitHub。
