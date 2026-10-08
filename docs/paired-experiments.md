# 可复核的成对实验

2026-10-08：新增 `ab` 与候选方案绑定的实验记录。实验由用户显式执行，不随模型生成方案自动运行，不自动采用改动或更新用户掌握。

## 协议与执行

协议为 JSON，包含 version=1、hypothesis、dataset_kind、grader="exact_output"、seed 和 cases。每个 case 有唯一 id、input、expected；最多 200 条。dataset_kind 说明样本来源，但运行器不认证标签来源。min_delta 可省略；省略时只报告 measured，不判赢家。

两组实现均为经过检查的 Python 文件：从标准输入读取同一 JSON（seed、cases 的 id/input），向标准输出写入 JSON 对象，status="complete"、results 为数组，每项包含 case_id 和 output。标签不写入标准输入；协议仍保存在本地，因此这里不宣称盲测。独立评分器比较输出与协议标签，布尔值与数字分开比较；缺项、重复、异常、超时及执行中脚本修改均使结果 inconclusive。

输出目录必须新建，保存协议、输入、两组脚本、评分器、哈希、Python 版本、标准输出/错误和 result.json。结果包含逐项改善/退步与准确率差值。显式 min_delta 达标且没有退步才报告 pass；pass 也不等于项目适用性、整体收益或允许采用。

这是普通本地子进程执行器，不是任意代码安全沙箱。会移除常见凭据环境变量并设置模型调用关闭标志，但没有网络隔离，也不能约束忽略该标志的代码。只执行已经检查的实现。外部依赖、硬件与完整环境没有自动冻结；需要额外锁定依赖才能保证复杂实验复现。旧 `experiment` 的脚手架指标与新独立输出评分不是同一验收方式。

## CLI

```powershell
.\.venv\Scripts\python.exe -X utf8 -m lodestar ab --protocol protocol.json --baseline baseline.py --candidate candidate.py --out workspace/my-ab
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch plan --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID --goal "待验证的问题" --mock
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch plans --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch experiment --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID --plan-id PLAN_ID --protocol protocol.json --baseline baseline.py --candidate candidate.py --out workspace/my-plan-ab
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch experiments --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID
```

候选实验要求属于该项目/候选的有效方案草案，保存 plan/read/assessment ID 与方案哈希，不能借用其他项目方案。模型方案仍待语义审核；实际实验结果单独保存，不能反向把草案标成已验证。

## 零 API 隔离演示

```powershell
.\.venv\Scripts\python.exe -X utf8 -m lodestar agent-pipeline-demo --out workspace/agent-pipeline-demo-new
```

此命令在读取应用配置前建立独立数据库，使用合成论文与 mock 评估/方案，串起读取、评估、方案、会话交接和实际成对实验。实验冻结实际 paper_context 函数，检查 6 个字段传递样本。2026-10-08 记录位于 workspace/agent-pipeline-demo-20261008：基线 3/6、当前函数 6/6，verdict=measured，模型调用和掌握事件均为零。这只是字段传递的功能诊断，不代表真实论文评估、语义准确性或用户掌握。演示不会覆盖已有输出目录。


## 记录复核与重跑

`python -m lodestar ab-check RESULT_DIRECTORY` 不执行脚本、不调用模型。它核对协议/输入/代码/评分器哈希，读取原始 stdout 重新评分，并核对差值、判定、lineage 和采用状态；失败返回非零退出码。consistent 只表示本地记录一致，不认证作者身份或标签来源。current_grader_differs 单独提示当前评分器版本与快照不同；复核使用当前 exact_output 规则，不执行归档评分器。

重跑仍使用显式 `ab`：将保存目录内 protocol.json、baseline/arm.py、candidate/arm.py 作为输入，选择新的 --out 目录。执行前仍需检查脚本；不自动执行下载的实验。2026-10-08 在 workspace/agent-pipeline-rerun-20261008 重跑已有六样本实验，逐项输出和差值相同，记录复核 consistent；当前评分器源码因增加复核功能而与原记录不同。此复现只覆盖上述自包含脚本和本机 Python，不代表复杂外部依赖实验复现。
