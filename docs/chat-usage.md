# 连续对话 CLI

```powershell
.\.venv\Scripts\python.exe -m lodestar chat start --user default
.\.venv\Scripts\python.exe -m lodestar chat send --session SESSION_ID --intent research --message "Harness 有什么新进展" --mock --offline
.\.venv\Scripts\python.exe -m lodestar chat send --session SESSION_ID --message "这个方法和之前的区别是什么" --mock
.\.venv\Scripts\python.exe -m lodestar chat send --session SESSION_ID --intent feedback --technology Harness --method "Dependency-Scoped Propagation" --feedback self_report --message "我理解了大致思路"
.\.venv\Scripts\python.exe -m lodestar chat history --session SESSION_ID
```

真实模型调用去掉 --mock；--offline 只控制检索夹具。反馈、开始和历史不需要模型凭据。

开始时可指定 --project-id ID；后续 --intent plan 复用已有论文和登记项目生成草案。
默认 --intent auto 使用保守规则路由；可显式覆盖。新信息检索、项目方案、方法补读与独立自评分开处理。复杂表达可能落到追问，当前未使用模型分类。研究结果持久化保存正文片段，追问只使用这些证据；没有重新检索就不声称更新。缺少证据需发起新的 research。

讨论、自评与尝试分别记录。普通追问不会静默提高掌握程度，用户的“懂了”也不是验证。当前没有自动评估复述正确性的能力。会话 user_id 隔离是本地数据边界，不是联网服务的认证系统。

离线会话夹具验证状态流转，不验证语言模型的追问质量。用户原始消息保存在会话中，讲解上下文使用最近的有界历史。

方法细节或方案每轮最多补读两篇已保存论文，使用隔离配置开启有界 PDF 正文读取。失败保留原证据，摘要降级不覆盖已有正文；补读记录写入 Trace。缺少明确技术上下文的自评先澄清，不写学习记忆。
