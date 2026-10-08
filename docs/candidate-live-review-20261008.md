# 候选到方案真实模型验收：2026-10-08

免费额度已重新确认：qwen3.8-flash 剩余 845.54K，2026-11-25 到期、用完即停开启。密钥由用户授权保存到 Git 忽略的 .env，默认调用仍关闭。真实连接检查成功。

独立数据库内登记 Lodestar，仅索引此前批准的 learning.py、retrieval.py、agent/project_plan.py 各最多 4000 字符。选择此前真实 watch 发现的 Raven（arXiv:2609.33439），先完成摘要路径，再修正验收脚本中 full_text 的错误属性为 full_text_enabled，读取 12000 字符正文片段。首次配置错误属于验收脚本，不是产品阅读全文开关。两次模型评估均 uncertain，方案均 draft、契约校验通过；每轮只调用 applicability 与 technical_plan，不执行修改，不写入掌握事件。

人工审核拒绝两份方案。摘要草案把已存在的研究/学习分离说成缺失，并建议依据文本关键词推断来源。正文草案推测整个 Agent 缺乏元数据隔离，提出把通用检索输入硬编码为 research_document；三份片段没有证明这些全局结论或调用方来源约束。论文的可组合模型-harness 单元也不能直接支持这个标签方案。引用存在和结构正确不能代替迁移逻辑审核。

保留 workspace/candidate-live-validation-20261008 与 workspace/candidate-live-body-validation-20261008 的 read/assessment/plan/usage/manifest/review。正文读取和两阶段 live 调用链路成立；技术建议质量本轮未通过，不计为真实方案完成或方法收益。

针对该失败，补充评估与方案提示约束：片段之外只能标未知；不能把 uncertain 变成已证实需求；来源不能从文本关键词或通用函数中的硬编码标签推断；不得否认已给出的现有隔离/门禁。提示修改不是语义正确性的自动证明，下一轮仍需人工审核。暂不执行任何被拒绝草案的 A/B。


## 后续复核：允许调查与无需改动

仅加提示约束的下一轮仍提出未经证明的元数据检查，记录在 workspace/candidate-live-revised-validation-20261008，人工审核拒绝。根因之一是原方案契约要求 1–3 项改动，即使找不到适用方法仍需填改动。

方案现有三种 action：propose_change、investigate、no_change。后两者要求明确理由和 changes=[]，保持引用与缺口校验；不允许启动绑定方案的实现 A/B。旧草案缺少 action 时按 propose_change 兼容。诊断协议可以保留，但不代表执行或采用。

workspace/candidate-live-action-validation-20261008 完成真实 12000 字符正文、uncertain 评估和 no_change 草案。人工审核认为本例无需改动的结论可接受：现有 learning.py 已限制 actor 与掌握事件，论文尚未提供具体可转移方法。不是把高层记忆概念强行改造成来源标签。保留真实输入/引用/用量/manifest/review；不声称一次运行证明整体语义可靠性，没有实验收益或掌握变化。


## 第二篇论文：Context Language Models

真实 watch 候选 arXiv:2609.37725 已读取 12000 字符正文片段；记录在 workspace/candidate-context-validation-20261008。该论文把上下文作为模型可编辑文件，是一个具体的上下文管理方法。真实评估返回 uncertain，草案选择 investigate、changes=[]，将后续工作限定为临时研究上下文的接口兼容与隔离检查。

人工审核接受其作为调查方向：可以在独立实验中比较可编辑临时上下文与受限 append-only 策略，但不能宣称当前整个项目都是 append-only，也不能编辑原始引文或用户掌握事件。输出为英文，未遵守中文要求，需要改进语言质量。当前没有完整 CLM 实现、论文基准复现或项目收益证明。下一步先完成本地接口/隔离调查，再准备固定任务和明确预算的受限原型；不能把受限原型说成论文完整实现。
