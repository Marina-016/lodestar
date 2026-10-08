# 临时研究上下文：受限原型与实际比较

2026-10-08。来源是 Context Language Models（arXiv:2609.37725）的“context as a file”方法，父评估/调查草案保存在 workspace/candidate-context-validation-20261008。这里研究临时上下文，不替代用户学习事件库，不修改原始引用，不自动采用方案。

## 原型边界

实现位于 eval/context_policy.py。模型返回新的工作笔记，应用仅在字符预算内接收并写入 context.txt，每次保存修订与哈希。sources.json 为独立原始材料记录，模型没有文件路径或工具执行入口。它是有界、受控的文件状态类比，未实现论文中的任意上下文函数、训练、缓存优化或多 Agent 系统。

append_only 策略由应用追加当前材料并保留末尾字符；model_edit 策略允许重写工作笔记。两组使用相同题目/材料顺序/模型/调用次数上限/输出上限，实际上下文和 Token 消耗不一定相同。每个材料处理一次，最终结果由协议中预先标注的答案及原始材料 ID 精确评分；标签不发送给模型。已知 ID 校验只证明材料已读，不证明引用支持答案。超预算编辑、未来材料引用或模型失败停止实验，不自动重试。

范围限定 1–4 个合成案例，每例 1–4 个材料，两组最多 32 次调用，每次输出上限 600 Token。验证模式不创建模型客户端或产物目录。live 必须由调用者明确确认当前选定模型免费额度和服务端用完即停，且本地调用开关开启；标志本身不检查云端额度，不能将它当成计费保证。

## CLI

```powershell
# 只验证协议、显示最大调用次数；不调用模型。
.\.venv\Scripts\python.exe -X utf8 -m lodestar context-policy-experiment --protocol protocol.json --out workspace/new-context-run
# 仅在当前免费额度/用完即停已核对时临时启用；不覆盖旧输出目录。
$env:LODESTAR_MODEL_CALLS_DISABLED = "false"
try {
  .\.venv\Scripts\python.exe -X utf8 -m lodestar context-policy-experiment --protocol protocol.json --out workspace/new-context-run --run-live --free-quota-confirmed
} finally {
  $env:LODESTAR_MODEL_CALLS_DISABLED = "true"
}
```

输出保存协议、实现快照、每组原始材料、逐轮模型请求/解析响应、上下文修订、最终结果及 usage.json。这是可检查的运行记录，不是带签名的来源证明；历史运行没有完整包版本记录，也没有确定性模型复现保证。`ab-check` 对应普通 Python 成对运行器，不能用于本原型记录。原型暂未接入正常研究 Agent，不能把 model_edit 误当成生产模式。

## 本次真实结果

workspace/context-policy-live-20261008：qwen3.8-flash，temperature=0，400 字符上下文，两个案例（早期事实保留、后续纠正），每组各六次调用。总计 12 次调用、5867 Token。

| 指标 | append_only | model_edit |
|---|---|---|
| 答案与原始出处均正确 | 1/2 | 1/2 |
| 早期事实案例 | UNKNOWN，未找到旧事实 | BLUE 答案正确，却引用无关的 p3 |
| 纠正案例 | GREEN，p3 | GREEN，p3 |

没有观察到联合指标提升，不判胜，不采用。可编辑上下文保存了事实值，却丢失原始出处，这是需要继续调查的失败。只有两例、每策略一次、固定执行顺序，不能推断总体质量或因果收益。协议与论文调查草案的关系另存 lineage.json，并明确该关联记录在执行后写入；不是事前预注册。

下一步保留此原始比较，设计来源绑定的受限编辑策略及包含未知/纠正/无关材料的固定案例，再做新输出目录下的比较。不能事后修改期望答案或用“只看答案”替换原先联合指标来宣称成功。用户掌握变化、项目收益与论文基准成绩均未测量。


## 来源绑定分支与复核

协议现在可用 modes 选择两个不同策略（append_only、model_edit、source_bound），默认仍比较前两者。source_bound 工作状态包含 notes 与最多四条原始 ID/精确引用，整个 JSON 仍计入同一字符预算。引用必须是已读原始材料的至少 12 字符子串，答案引用必须属于保留的绑定；未知/未来 ID、重复 ID、错误原文和超预算均拒绝。模型未获得文件工具，来源记录与工作状态分开。这只检查字面来源，不判语义支持。

首次真实分支运行 workspace/context-policy-bound-live-20261008 在第 8 次调用后 inconclusive（4341 Token）：第二轮遗漏顶层 bindings，上一版有效状态保留。统一提示中的 JSON 格式后，在新目录 workspace/context-policy-bound-schema-live-20261008 完成同一协议：追加式 1/2、来源绑定 2/2，均按原有答案/出处联合标签评分。上一轮失败和期望答案未改写。用量保存在 usage.json，逐轮资料与人工说明分别在 record.json、review.json。

这两个案例专门对早期事实被末尾截断和后续纠正施压，并已用于诊断格式问题；不是独立保留的测试集，也不代表真实项目任务。不能据此选择生产策略、宣称论文成绩或总体收益。原型仍独立于正常 Agent，未更新用户学习事件。后续若评估生产使用，必须补独立案例、完整环境记录、来源语义审核和项目级质量验收。


## 记录复核与项目候选登记

`context-policy-check RESULT_DIRECTORY` 不调用模型或执行代码：核对协议/实现哈希、材料记录、逐轮固定输入、修订、最终输出与评分。consistent 仅表示本地记录一致，不认证执行身份或语义支持。早期缺少哈希的原型记录不能通过此入口，需要保留为历史材料，不能补写字段伪装成原始验收。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m lodestar context-policy-check workspace/context-policy-bound-schema-live-20261008
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch investigation --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID --plan-id PLAN_ID --result-directory RESULT_DIRECTORY
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch investigations --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID
```

登记要求契约有效且 action=investigate 的草案，协议声明的 paper 必须属于该草案的论文证据，不能借用其他候选/论文。随后复核本地记录，保存 result 快照及哈希、project/recommendation/read/assessment/plan ID 和方案哈希。它是执行后的结果登记，不是预注册，也不替代实施型实验。不会把 imported_local_record 标成经过认证的执行，不更新采用或掌握；原文件后续变化也不会重写已保存快照。

已在 Context Language Models 候选的独立真实验收数据库完成 CLI 登记与历史读取，记录为 investigation #1。注册过程模型调用为零、mastery_effect=none；原始结果仍标注两案例的局限。成功与 inconclusive 原始记录均可复核；篡改评分、材料输入、实现或绑定其他论文会被拒绝。


## 运行环境与复现

2026-10-08 起的新运行同时保存 environment.json、requirements-frozen.txt 和 environment_hashes，包含 Python/平台及已安装 Python 包的精确版本，排除环境变量、凭据、安装源 URL 和路径。版本清单不包含本地 Lodestar 的安装地址；复跑还需相同源码版本。ab-check / context-policy-check 对声明的环境记录校验哈希；缺失或篡改会使记录不一致。旧结果不补写当时未记录的环境，也不能当作完整环境冻结。

requirements-frozen.txt 用于在相同 Python/平台的隔离环境重建包版本，并非带 wheel 哈希的供应链锁定；它不锁定 OS 依赖、包索引内容或远端模型权重。代码实验可以重新执行固定输入；模型调查的记录可以重新评分，但再次 API 调用是重新采样，不能保证相同输出。不要为了复现旧模型结果而自动耗用新额度。


## 当前版本与环境链最终验收

workspace/context-policy-final-environment-20261008 使用当前版本从 CLI 真正运行同一协议，protocol SHA 与之前一致；12 次 qwen3.8-flash 调用、7142 Token。运行前控制台免费余额 723.54K、用完即停开启；没有项目代码或用户记忆输入。两组答案/原始出处联合指标仍为 append_only 1/2、source_bound 2/2。案例和期望答案未改，仍是已知合成诊断集，不能作独立泛化或总体收益证明。

本轮 environment.json、requirements-frozen.txt 在运行时采集，并由结果哈希关联；context-policy-check consistent，协议、实现、逐轮输入、修订、评分与环境记录均已复核。随后将该诊断登记到独立数据库副本中的历史 CLM 调查草案 #1，保存新的 investigation #2；没有将它挪用为新中文优先级片段草案的效果。原来的成功/失败/无环境记录保留原样。
