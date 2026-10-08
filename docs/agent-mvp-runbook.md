# Agent 后端 MVP 启动与验收

2026-10-08：后端研究、追问、技术/方法接触、项目订阅、候选评估与三类草案已有实现和运行记录。前端暂缓。当前后端目标已完成逐项验收；逻辑见 [PRD](agent-prd.md)，证据与限制见 [目标台账](agent-goal-audit-20261008.md)，10-04 历史结果见 [验收报告](agent-mvp-acceptance.md)。

## 安装与不产生模型费用的演示

在项目根目录使用 Python 3.10+（本机验证为 3.11），运行 `python -m pip install -e .`。本机使用 `.venv`。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m lodestar agent-demo --out workspace/my-agent-demo
# 本机验收记录的显式回放：输出目录必须不存在。
.\.venv\Scripts\python.exe -X utf8 -m lodestar agent-demo --out workspace/my-recorded-demo --recording workspace/mvp-demo-recording
```

默认演示不读取 .env、不联网、不创建真实模型客户端，使用独立数据库运行研究、追问、反馈和历史持久化。回放模式复制历史输出，manifest 标记 recorded_replay，模型调用为零。录制数据不随源码分发，缺失文件时明确失败。回放中的 provenance.json 区分研究会话、另一次方法记忆验证和单独项目方案验证；它们不是同一次端到端运行。

已验证目录：workspace/mvp-acceptance-offline-demo、workspace/mvp-acceptance-replay。两者都不是实时资讯服务。

## 连续对话与隔离数据


以下 PowerShell 环境变量只影响当前终端。设置新的 DB 路径可避免写入原有用户数据。

```powershell
$env:LODESTAR_DB_PATH = "$PWD/workspace/my-session/agent.db"
$env:LODESTAR_WORKSPACE_DIR = "$PWD/workspace/my-session/tasks"
$env:LODESTAR_MODEL_CALLS_DISABLED = "true"
.\.venv\Scripts\python.exe -X utf8 -m lodestar chat start --user me
# 将上一行返回的 conversation_id 填入 SESSION_ID。
.\.venv\Scripts\python.exe -X utf8 -m lodestar chat send --session SESSION_ID --user me --intent research --message "agent memory research" --mock --offline
.\.venv\Scripts\python.exe -X utf8 -m lodestar chat send --session SESSION_ID --user me --intent followup --message "解释这个方法" --mock --offline
.\.venv\Scripts\python.exe -X utf8 -m lodestar chat send --session SESSION_ID --user me --intent feedback --technology Harness --method example --feedback self_report --message "理解了大意"
.\.venv\Scripts\python.exe -X utf8 -m lodestar learning list --user me
.\.venv\Scripts\python.exe -X utf8 -m lodestar chat history --session SESSION_ID --user me
```

更多路由、方案与记忆规则见 chat-usage.md、agent-development.md。研究知识缓存不是用户掌握记忆。模型解释或用户说懂了均不自动提升掌握；示范类事件目前依赖调用方提供人工核验的证据，没有自动判卷器。

## 模型与额度保护

本机 .env 使用 dashscope/qwen3.8-flash，judge 同型号，LODESTAR_MODEL_CALLS_DISABLED=true。默认阻止 live 客户端初始化。总开关不检测免费额度；服务端“用完即停”才防止额度耗尽后继续计费。

本次控制台确认 qwen3.8-flash 有免费额度、有效期至 2026-11-25、用完即停开启。充值后最小调用 HTTP 200，后续真实研究与方案调用成功。之前 403 和被拒绝方案保留在历史记录中，不代表当前状态。没有付费模型回退。

只在当前账号、业务空间、模型额度和有效期确认后临时启用调用；密钥只在本地设置，不提交。配置不代表未来免费额度仍有效。官方规则见 https://help.aliyun.com/zh/model-studio/new-free-quota 。

## 真实 CLI 调用


只在当前账号模型额度、有效期和用完即停均已确认时使用。账号欠费或无权限返回错误时停止，不切换其他模型。新账号不能沿用本次额度判断。

```powershell
$env:LODESTAR_LLM_PROVIDER = "dashscope"
$env:LODESTAR_MODEL = "qwen3.8-flash"
$env:LODESTAR_JUDGE_MODEL = "qwen3.8-flash"
$env:LODESTAR_LLM_BASE_URL = "https://ws-toz7p9l3xgfl4p1r.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
$env:DASHSCOPE_API_KEY = "<在本地填入，不要提交或发到聊天>"
$env:LODESTAR_FULL_TEXT = "true"
$env:LODESTAR_FULL_TEXT_MAX_SOURCES = "1"
$env:LODESTAR_MODEL_CALLS_DISABLED = "false"
try {
    .\.venv\Scripts\python.exe -X utf8 -m lodestar model-check
    # 只有上一步返回 status=ok 才继续。
    .\.venv\Scripts\python.exe -X utf8 -m lodestar chat send --session SESSION_ID --user me --message "今天 Agent Harness 有哪些新论文？" --days 7
} finally {
    Remove-Item Env:DASHSCOPE_API_KEY
    $env:LODESTAR_MODEL_CALLS_DISABLED = "true"
}
```

默认模型超时 120 秒，每次输出有 Token 上限，方案最多一次有界修复。发生综合失败时任务为 error，已读片段保存为 read_evidence.json；绑定会话仍可用 followup 继续解释证据，不必先重新抓论文。没有额外自动模型重试，也没有免费失败后付费回退。


## 项目方案与后续范围

本机远端验收只发送用户授权的 learning.py、retrieval.py、agent/project_plan.py 三个有界片段，不能据此自动发送整个仓库。登记、索引、方案命令见 [对话使用说明](chat-usage.md)。方案原文引用和结构校验不等于语义正确，必须审阅 draft 后再决定是否实施。

已实际运行字段传递的六样本成对代码实验及论文上下文方法的受限真实模型诊断；后者仅两个已知合成案例，不能代表项目收益。propose_change / investigate / no_change 分支、结果关联和新运行的环境版本记录已实现。自动掌握判卷、新 UI、技术别名归一和语义召回仍需后续完善。

2026-10-08：主动论文发现已增加订阅、候选收件箱及可运行轮询进程，使用说明见 [主动论文发现](proactive-paper-discovery.md)。候选语义评估和结构草案有显式命令；未自动逐篇生成方案，未安装永久后台服务。


当前范围以 [Agent PRD](agent-prd.md) 为准。候选到方案及 A/B 的零 API 演示使用 `python -m lodestar agent-pipeline-demo --out workspace/new-pipeline-demo`；输出目录必须不存在。实验协议见 [成对实验](paired-experiments.md)。


## 当前链路与复核入口

无需 API 的两个演示（每次使用不存在的输出目录）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m lodestar agent-demo --out workspace/new-agent-demo
.\.venv\Scripts\python.exe -X utf8 -m lodestar agent-pipeline-demo --out workspace/new-pipeline-demo
.\.venv\Scripts\python.exe -X utf8 -m lodestar ab-check workspace/new-pipeline-demo/ab
```

项目订阅和候选的实际命令顺序是 watch add → tick/worker → inbox → read → evidence/handoff → assess → plan。模型门禁和三文件授权仍适用。propose_change 审阅两组实现后显式 watch experiment；investigate 使用独立协议运行诊断，再 context-policy-check 和 watch investigation；no_change 保留理由。详细参数见 proactive-paper-discovery.md、paired-experiments.md、context-policy-investigation.md。

追问现在也提取 supported explained 方法接触，提取失败保留回答及审计元数据，不提升掌握。程序附上实际提供的论文链接及有界范围，来源清单不代表逐句核验。真实验证已确认双重引用和 unknown 掌握；结构化输出区分论文事实、推测、缺口和一般知识，引用不匹配的陈述不交付。当前代码回放已将来源保存机制列为缺口；这只验收本次问题，不是通用语义质量保证。
