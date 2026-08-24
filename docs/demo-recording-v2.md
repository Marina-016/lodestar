# Lodestar Demo 录制脚本（逐镜头版）

## 这段 Demo 要证明什么

Lodestar 不只是回答“本周有什么热点”，而是把热点变成一条可审计的产品工作流：

本周热点扫描 → Agent 解释概念 → 关联 Lodestar 项目 → 打开原始论文 PDF → 展示 Agent 轨迹 → 用户确认记忆 → Knowledge State 更新 → 生成实验骨架。

录制时请把“用户动作”和“AI 实际输出”都录进去。下面的 AI 输出是当前演示数据对应的页面原文，可直接作为字幕或旁白依据。

## 录制前准备

- 打开：[http://127.0.0.1:8123/](http://127.0.0.1:8123/)
- 当前模式：演示回放，页面会显示“演示模式 · 预置研究回放”。不要说成实时联网生成。
- 工作区包含：1 个项目、8 个 Knowledge State 概念、4 条研究记录、13 条来源、4 个实验。
- 项目：`Lodestar / Agent Research Lab`
- 录制前执行：`python -m lodestar demo reset`

## 0–8 秒：先展示项目上下文

用户动作：打开“我的项目”，停留在项目卡片，然后返回“研究对话”。

画面中要出现：

```text
Lodestar / Agent Research Lab
进行中
Agent、Memory、Eval、MCP、Trace、Python
```

旁白：

> 这是我的 Agent Research Lab。Lodestar 会先理解我正在做的项目，再判断一篇新论文是否值得进入产品路线，而不是只给我一份热点列表。

## 8–25 秒：扫描本周 Agent 热点

用户输入（逐字）：

```text
本周 Agent 研究有哪些值得 Lodestar 优先验证的新进展？
```

等待研究卡片完成后，画面中应出现以下 AI 输出：

```markdown
# 本周 Agent 研究有哪些值得 Lodestar 优先验证的新进展？

> 一句话结论：最新批次呈现出一条共同主线：长期状态和可复用能力不是越多越好，Agent 需要判断什么值得进入上下文、哪些来源真正独立、以及一次成功经验能否迁移。结合 Lodestar 当前代码，优先级最高的是可信记忆。

## 关键信号

### 01 · 可信记忆门控
概念：记忆不是越多越好，Agent 需要在写入上下文前判断相关性、来源和风险。
与当前项目的关系：对应 Lodestar 的 Knowledge State、记忆复核和 Memory Trust Gate，是当前最直接的产品能力缺口。
关键来源：MemTrapBench: Benchmarking Cognitive Traps in LLM Memory Use
查看 PDF 原文 ↗

### 02 · Skill 的跨任务迁移
概念：可复用经验应拆成粒度合适的 Skill，而不是把一次成功轨迹原样复制。
与当前项目的关系：可以把 Lodestar 的 Research Trace 和 Eval Gate 连接起来，筛选真正能迁移的 Skill candidate。
关键来源：Break It Down, Pass It On: Cross-Task Skill Transfer in LLM Agents
查看 PDF 原文 ↗

### 03 · 预算感知的上下文选择
概念：工具和 Skill 的选择要同时考虑任务收益、冗余和 Token 成本，而不只是逐条做相似度 Top-K。
与当前项目的关系：对应 Lodestar 的 Tool Registry、Harness 和 Context Budget，可直接转成一个可评估的选择器实验。
关键来源：Optimal Skill Selection for LLM Agents with Provable Bicriteria Guarantees
查看 PDF 原文 ↗
```

旁白：

> 这里不是简单列出三篇论文，而是先把每个热点解释成一个 Agent 概念，再说明它和 Lodestar 当前能力的连接点。每个热点都保留原始论文名，并可以直接打开 PDF。

## 25–38 秒：展示项目关联度和代码证据

用户动作：向下滚动到“项目关联”。

AI 输出应出现：

```text
项目关联度：94/100（高）
评分构成：技术栈命中 35/35 · 项目语境命中 20/25 · 代码证据 24/25 · 进行中状态 15/15
观察到的缺口：Lodestar 可以扫描论文并建立项目代码索引，但每周热点还需要按照具体的实现缺口排序。
关联原因：当前项目画像和代码索引，让可信记忆比只关注模型训练的方向更容易落到产品实现。
接入位置：热点筛选 → 项目证据 → 研究简报
```

继续展示：

```text
命中的项目代码：
- lodestar/frontier.py
- lodestar/relevance.py
- lodestar/project_index.py
```

旁白：

> 这个 94 分不是模型主观打分，而是由技术栈命中、项目语境、代码证据和项目状态组成。它把“这篇论文有意思”变成“这篇论文对我的项目有多值得做”。

## 38–48 秒：打开热点里的原始 PDF

用户动作：点击“01 · 可信记忆门控”下面的“查看 PDF 原文 ↗”。

浏览器打开：

```text
https://arxiv.org/pdf/2608.20202
```

画面要求：

1. PDF 在新标签页打开。
2. 快速展示论文标题页。
3. 返回 Lodestar，保留研究卡片和证据链。

旁白：

> 论文不是装饰性引用。用户可以从热点卡片直接跳到原始 PDF，再回到项目关联和 Agent 轨迹继续审计。

## 48–62 秒：展开第二阶段研究输出

用户输入（逐字）：

```text
展开第 1 条可信记忆，告诉我它具体和 Lodestar 哪些代码有关，以及应该怎么验证。
```

AI 输出应出现：

```text
# 可信记忆：Agent 如何判断一条记忆该不该被使用？

一句话结论：真实且相关的记忆也可能造成推理固化；多个 Agent 还可能重复引用同一上游来源，制造虚假多数。对 Lodestar 来说，召回之后、注入上下文之前需要一层可审计的 Memory Trust Gate。

关键信号：
01 · 相关记忆导致推理固化
概念：即使记忆与问题相关，也可能把当前推理锁定在错误路径上。
与当前项目的关系：Lodestar 需要在 memory/repo.py 到 agent/loop.py 之间加入 Trust Gate，而不是直接注入召回结果。

02 · 同源记忆不能重复计票
概念：多条记忆如果都来自同一个上游证据，不能被当作多个独立支持。
与当前项目的关系：项目的记忆审计轨迹需要记录 provenance，避免 Knowledge State 把重复来源误判成共识。

03 · 记忆决策的四个风险维度
概念：召回前要同时判断相关性、来源独立性、冲突风险和时效性。
与当前项目的关系：这四项可以成为 Lodestar 的 Memory Trust Gate 字段，并进入 Eval 的 memory trap、false-majority 和任务正确率指标。

项目关联度：95/100（高）
命中的项目代码：memory/repo.py、agent/loop.py、trace/recorder.py、tools/knowledge.py

可验证方向：在 memory/repo.py 的召回结果进入 agent/loop.py 前增加 Memory Trust Gate。
下一步：比较 Top-K 直接注入与 Trust Gate 两组，测 memory trap rate、false-majority rate、任务正确率和 Token 成本。
```

## 62–72 秒：展示可审计 Agent 轨迹

用户动作：展开“Agent 轨迹”。

画面中应出现这些步骤：

```text
启动演示回放
加载整理来源
关联项目代码 · 关联度 95/100
评估记忆风险
提出记忆更新
等待用户确认记忆
```

旁白：

> 关键不是最后一段回答，而是 Agent 为什么得到这个结论。来源、项目代码、记忆风险和更新提议都绑定在同一条 Trace 上。

## 72–82 秒：用户确认记忆

用户动作：勾选 `Memory Trust Gate`，点击“记住选中的结论”。

AI 系统消息应出现：

```text
已记住：Memory Trust Gate
```

然后打开“知识库”，展示新增概念：

```text
Memory Trust Gate
状态：known
置信度：medium
```

旁白：

> Lodestar 不会默默改写长期记忆。只有用户明确确认，研究结论才会进入 Knowledge State，并保留来源和确认轨迹。

## 82–94 秒：从洞察生成实验骨架

用户动作：打开“实验项目”，找到 `Memory Trust Gate`，点击“生成骨架”。

实验卡片应出现：

```text
Memory Trust Gate 能否在不降低正常任务正确率的前提下，减少 memory trap 与 false majority？

用相同问题和上下文预算比较 Top-K 直接注入与 Trust Gate 两组，重点测 memory trap rate、false-majority rate、任务正确率和 Token 成本。

状态：scaffolded
```

旁白：

> 最后不是宣称方案已经有效，而是把研究洞察转成可执行的对照实验：明确 baseline、candidate、指标和上下文预算。

## 94–100 秒：收束

回到研究简报或项目卡片。

旁白：

> 这就是 Lodestar 的闭环：发现新进展，解释概念，关联项目，打开原始论文，留下可审计轨迹，经用户确认更新记忆，再把洞察变成实验。

## 录制时不要说错

- 不要说“这是实时联网生成”；页面当前是预置研究回放。
- 不要把论文提出的 Memory Trust Gate 说成已经验证有效。
- 不要只展示用户输入；每次都停留到 AI 输出完整出现。
- 不要跳过 PDF 点击、项目关联度、Agent 轨迹和用户确认记忆这四个证据点。
- 论文标题、PDF 链接、代码文件名和指标名称保留原文，解释部分使用中文。
