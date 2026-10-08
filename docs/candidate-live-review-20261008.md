# 候选到方案真实模型验收：2026-10-08

免费额度已重新确认：qwen3.8-flash 剩余 845.54K，2026-11-25 到期、用完即停开启。密钥由用户授权保存到 Git 忽略的 .env，默认调用仍关闭。真实连接检查成功。

独立数据库内登记 Lodestar，仅索引此前批准的 learning.py、retrieval.py、agent/project_plan.py 各最多 4000 字符。选择此前真实 watch 发现的 Raven（arXiv:2609.33439），先完成摘要路径，再修正验收脚本中 full_text 的错误属性为 full_text_enabled，读取 12000 字符正文片段。首次配置错误属于验收脚本，不是产品阅读全文开关。两次模型评估均 uncertain，方案均 draft、契约校验通过；每轮只调用 applicability 与 technical_plan，不执行修改，不写入掌握事件。

人工审核拒绝两份方案。摘要草案把已存在的研究/学习分离说成缺失，并建议依据文本关键词推断来源。正文草案推测整个 Agent 缺乏元数据隔离，提出把通用检索输入硬编码为 research_document；三份片段没有证明这些全局结论或调用方来源约束。论文的可组合模型-harness 单元也不能直接支持这个标签方案。引用存在和结构正确不能代替迁移逻辑审核。

保留 workspace/candidate-live-validation-20261008 与 workspace/candidate-live-body-validation-20261008 的 read/assessment/plan/usage/manifest/review。正文读取和两阶段 live 调用链路成立；技术建议质量本轮未通过，不计为真实方案完成或方法收益。

针对该失败，补充评估与方案提示约束：片段之外只能标未知；不能把 uncertain 变成已证实需求；来源不能从文本关键词或通用函数中的硬编码标签推断；不得否认已给出的现有隔离/门禁。提示修改不是语义正确性的自动证明，下一轮仍需人工审核。暂不执行任何被拒绝草案的 A/B。
