# V0 质量版首轮实施与验收 · 2026-10-17

基线 `7d09636`，开发分支 `codex/v0-stability-20261017`。本轮完成可靠性修复与可执行质量验收基础；没有新增真实模型调用、收费搜索、模型切换、真实用户库迁移或公网部署。研究语义仍待真实验收，不宣布质量版已全部完成。

## 实际修复

1. **启动依赖**：Anthropic 按提供商惰性导入。mock / DashScope 的独立进程可以在禁止导入 Anthropic/httpx2/httpcore2/truststore 时初始化，实际探针约 1.17 / 1.09 秒。Windows 实验凭证用 sys.getwindowsversion 和 Python build target，不查询 WMI。
2. **隐藏重试**：SDK 明确 max_retries=0；删除应用层 HTTP fallback 和空文本自动倍增预算/改变思考设置。旧判断把 `thinking={'type':'disabled'}` 也视为真值，导致 HTTP 错误悄悄再发一次，已通过失败内存传输复现。现 429/500/529、连接失败、空文本均明确退出；一次有界 invalid_json 修复保留并计数。
3. **任务槽位**：Thread 构造/start 失败原先留下 queued 与 cancel signal，阻止该空间之后发送。现在持久化安全终态、保留输入/失败记录、释放信号和槽位，原启动异常继续抛出。同 request key 幂等，新请求可再次运行；已回应的取消保持 cancelled；子任务 created_not_submitted 回执不伪装成提交成功。
4. **流格式**：真实 SDK + 内存 SSE 复现 CR 换行数据型错误被误归为空流。现在处理 LF/CRLF/CR、跨块换行和 MIME 大小写，保留之前的文字、不重写成功字节。DashScope 流内错误也使用明确 upstream_stream_error 分类。
5. **验收资源和费用边界**：Anthropic 与 DashScope 的隔离客户端共用物理请求尝试上限；DashScope 用各自独立 transport，避免全局 requests patch 和并发混算。只记录安全 HTTP 元数据，不保留响应正文。未启动的 HTTP 服务只 server_close、不调用会永久等待的 shutdown。
6. **测试资源**：旧 smoke 测试原先用 mkdtemp 遗留 12 个目录，改为 TemporaryDirectory + addCleanup，业务断言不变。未忽略清理错误。

## 质量验收基础

新增 [计划](v0-quality-plan-20261017.md)、[八组人工验收卡](v0-quality-cases.md)、QF-1 合成论文夹具及 `scripts.eval_workspace --scenario quality`。

四轮固定证据场景覆盖近期发现、方法/实验比较、发表/数字判断和两句追问。来源、日期、正文位置与版本被冻结；报告保存 source-snapshot.json 和 SHA-256，人工事实表单独保存在 human-facts.json，不进入工具结果或模型输入。固定时间注入只存在于隔离验收器，不修改生产检索。合法工具参数通过实际 dialogue._execute 契约测试，包括发现工具的 limit。

功能/覆盖与语义分别记录，不用关键词、字数、非空、completed 或模型自评判研究正确。QF-1 全部虚构，不作为真实论文证据；默认 mock CLI 没有自主检索，不能把四轮 completed 当作论文研究质量通过。

## 已执行与结果

| 检查 | 结果和证明范围 |
|---|---|
| 冻结后的完整 Python 离线回归 | 375/375，失败/错误/跳过均 0，约 105.5 秒；无 WMI 降级；前后源码哈希一致 |
| 前端 | 14/14；app.mjs 语法通过 |
| 本地构建条件 | Python compileall、pip check、git diff --check、发布预检 311 文件均通过 |
| 测试资源 | 专属临时根为空；本轮测试进程退出；残留来源与清理记录保留 |
| 默认 quality CLI | 四轮本机 HTTP 完成，model_attempts=0、无遗留运行、semantic_review=not_applicable_offline_fixture；只证明默认命令和持久报告 |
| 固定源工具 HTTP 集成 | 合成模型实际调用检索/正文/发表工具；仅允许 loopback、源哈希正确、参考答案未进入模型、结束后 patch 恢复；不是自主模型语义验收 |
| 当前公开 arXiv | generic agent memory 检索返回 2 条 10-15 记录，摘要 1625 / 1315 字符；只是检索候选，不等于适合个人研究 Agent |
| 公开有界正文 | 单独显式开启全文后，候选一返回 PDF 查询选段，full_text_ok=true、query_matched=true、3 个原文坐标，约 6000 字符；不是全文或事实性结论 |

证据在忽略目录 workspace/acceptance：offline-qa-frozen-20261017、quality-cli-20261017、public-paper-20261017、public-paper-body-20261017。公开检索首轮没有开启全文，保留其摘要级观察；第二轮明确开启并单独记录正文，没有覆盖首轮。

## 原始失败与尚未解决

- 修改前 90 秒硬上限的完整回归超时，栈停在 HTTP shutdown；单项复核通过。最终同环境完整集合约 105 秒，不能将最初超时直接判为确定性死锁。旧日志与中间 359/362/366 项记录保留。
- 当前虚拟环境 Python 3.11.9 没有历史报告里的 platform._wmi_query。本次没有复现或证明修复旧 WMI 系统根因；Anthropic 真实路径仍会加载上游 SDK/信任库。惰性导入和本地凭证去 WMI 仅减少无关路径暴露。
- 10-15 上游服务为什么返回流内错误仍未知。格式兼容、错误分类和内存测试不证明服务端已恢复。
- 真实 Harness 理解、论文相关性/数字/发表/引用、父子任务汇总与真实记忆连续体验仍未重新通过。没有通过离线夹具将这些旧失败改成成功。
- 本地配置只读核对为 DashScope，模型 qwen3.8-flash，真实调用开关关闭；没有改 .env。百炼额度页连续读取超时，当前免费额度未确认；已单独询问是否允许在确认免费额度及用完即停后，进行最多 24 次隔离请求。待答复期间继续离线和公开论文工作，不越过开关。

下一步按计划完成受控真实模型语义标注、同语境的不同自然表达、双任务真实汇总和共享记忆连续验收，再评估通用搜索与后续产品扩展。

SSE 换行与 MIME 处理参考 [WHATWG Event Streams](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream)。

## 用户确认后的真实续检与修复

用户随后明确确认当前qwen3.8-flash免费额度及用完即停，允许最多24次隔离请求。本机默认开关保持关闭，仅验收进程副本启用；第一段quality12次、第二段workspace12次，实际合计24次，全部HTTP200，结束后无遗留运行。当前授权已用完，本地账本live-budget-20261017.json不允许自动重置/续发。

真实模型+固定合成证据的前三轮均有回答，但人工语义失败：未标注推断、片段范围外推、比较引用缺失，以及额外效率指标的单位问题。发表未知与12个百分点/相对20%/输入token减少20%的正确项单独保留；第四轮精简追问被acceptance_budget在请求前阻止，未执行。详见 [逐条人工审阅](v0-quality-semantic-review-20261017.md)。不能把不同模型与旧基线表现差异归因于本轮代码。

workspace实际创建两个独立任务，两条回执对应真实任务；子任务均completed，禁止检索策略、实际无外部工具分发、后台学习事件为空的检查通过。Harness含义在自包含派发指令和子回答中保持Agent工具/上下文/运行控制语境。但父模型先用六次read_task忙轮询，主派发回合消耗九次模型请求；两个子任务各一次，汇总首请求后耗尽十二次。因此汇总回答、随后记忆保存/纠正未完成，不能把两子任务成功扩展为整轮通过。

据此扩展现有read_task的可取消短等待：默认10秒，wait_seconds=0立即返回，上限20秒；超时返回状态，不取消/重启子任务。等待期间不持应用锁或数据库连接，run与消息同一SQLite快照，保留来源和退休上下文过滤。新增9项确定性离线测试通过；没有追加真实调用，因此节省模型请求只是待真实复测的设计目标，不宣称已证实模型不再忙轮询。

人工审阅还发现Windows write_text换行转换使原source-snapshot.json文件字节与声明SHA不一致，而规范化文本哈希一致。旧quality-live/CLI产物和错误manifest保留。未来快照改用UTF-8 write_bytes，回归验证read_bytes的实际SHA，相关14项通过。此修复不改变旧数据为已验证，也不能将旧规范化文本一致宣称为原字节契约通过。

## 后续修复后的最终冻结检查

含任务等待和实际字节哈希修复的最终集合：384/384 Python离线、14/14前端通过，零失败/错误/跳过，无WMI降级；约92.1秒，源码哈希前后一致，临时根为空。JS语法、Python编译、pip check、git diff --check与发布预检314文件均通过。证据目录为workspace/acceptance/offline-qa-task-wait-20261018。仅文档随后补记录，不再修改冻结代码。

未来写入的QF-1快照已有独立原字节检查：source-bytecheck-20261018的declared/actual SHA均为504fe185dfb2992821a9214bb056974eb3b727cbfe5c2687c4342045abe09c1a。它只证明新写入契约，不替代此前真实输出的人工失败结论。
