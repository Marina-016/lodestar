# Demo Recording Runbook

这份文档只负责录制前的环境准备与验收。正式台词见 [demo-recording-v2.md](demo-recording-v2.md)。

## 1. 重建干净数据

在项目根目录执行：

~~~powershell
$env:LODESTAR_DEMO_REPLAY="true"
python -m lodestar demo reset
~~~

reset 会备份当前数据库，再清空应用状态并写入可信记忆主题的数据集。它不会删除源码、Git 历史或用户工作区外的文件。

## 2. 启动本地预览

~~~powershell
$env:LODESTAR_DEMO_REPLAY="true"
python -m lodestar ui --port 8123 --no-browser
~~~

预览地址：

http://127.0.0.1:8123/

## 3. 录制前检查

~~~powershell
Invoke-RestMethod http://127.0.0.1:8123/api/demo/readiness | ConvertTo-Json -Depth 6
~~~

同时在页面确认：

- 项目数为 1。
- Knowledge State 为 8。
- 对话页面为空。
- 历史记录只包含 4 条演示研究。
- 实验页包含 4 个实验。
- 页面能看到“演示模式 · 预置研究回放”。

## 4. 两条固定输入

第一条：

> 本周 Agent 研究有哪些值得 Lodestar 优先验证的新进展？

第二条：

> 展开第 1 条：可信记忆。结合最新论文和 Lodestar 现有代码，告诉我为什么相关记忆也可能伤害推理，以及应该怎么验证。

不要临时改写关键词。“本周 + Agent”用于进入 Frontier 选择步骤，“记忆 / Memory”用于进入可信记忆研究步骤。

## 5. 录制验收

- 第一条回答不给出待确认记忆，只要求用户选择方向。
- 第二条回答展示 4 条论文证据和至少 3 个项目代码位置。
- Agent Trajectory 包含“评估记忆风险”。
- 点击确认后，Knowledge State 新增 Memory Trust Gate。
- 实验点击“生成骨架”后状态为 scaffolded。
- 整个流程不调用实时模型，不消耗 ChatGPT/Codex 额度。
- 正常 live 对话、Luna 模型限制和本机 Codex Harness 配置不被修改。

## 6. 隐私与发布

- 不录制 .env、终端密钥、Git 凭据或本地用户名。
- lodestar/data、workspace、experiments、PDF 缓存、视频帧和本地工具目录均保持 Git ignore。
- GitHub 只提交源码、测试、示例配置和文档。
