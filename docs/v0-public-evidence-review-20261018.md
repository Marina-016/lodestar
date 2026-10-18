# 冻结公开论文验收入口 · 2026-10-18

本轮加入 `public-quality` 场景，使用已经取得的真实公开论文正文快照，替代只能使用 QF-1 虚构材料的限制。它用于隔离取证决策与材料理解问题，不代表最新检索、联网服务、发表核验或回答语义已经通过。默认不调用真实模型。

## 验收统计修复

旧 `observed_coverage.body_reads` 只依据请求参数 full_text=true 与无错误，摘要回退也可能被误记为正文读取。现在动作报告保留实际 read_depth、coverage、full_text_ok、query_matched 及返回文本字符数，按实际结果统计：

- body_read_requests：已执行的正文请求。
- body_reads：实际返回非空正文、无工具错误、且未明确查询无匹配的读取。
- abstract_fallbacks：请求正文但实际返回摘要。
- unmatched_body_reads：正文已提取但没有查询匹配。
- publication_lookups：有实际发表观察状态的核验；冻结公开库返回 not_checked 不计为完成核验。

覆盖统计在每轮保存报告时更新，失败前已经发生的结果也可以复核。这些都是功能观察，不是语义评分。旧历史报告不重写；复核旧 body_reads 时仍须查看原始实际阅读结果。

## 材料冻结与隔离

`scripts.public_paper_fixtures` 读取已有 collection-status、官方元数据记录及 PDF 缓存。冻结产物保存版本化 arXiv URL、题名、摘要、作者、首次提交/修订/获取时间、提取正文、正文哈希和原 PDF 哈希。正文哈希在加载时校验；既有缓存的来源身份仍依赖之前的采集记录，不声称哈希本身证明了论文来源或正确性。

快照为独立 JSON，无运行时目录引用。schema 不接纳额外人工答案字段，候选检索只返回元数据和摘要，正文必须经 read_paper 返回。读取复用当前 PDF 有界摘录逻辑，保留实际偏移和覆盖范围。未知来源、网页、在线发现等未支持的操作返回错误，无真实工具或外网回退。发表状态保持 not_checked，不虚构新核验。

检索只在 2–8 篇冻结材料中做普通词法 AND 匹配，可用多个独立查询；首次提交日期窗口仍生效，不把近期修订冒充新稿。该检索的覆盖范围比真实 arXiv 更小，不能据此评估在线搜索召回能力。

## 使用入口

仅在已有公开采集资料上生成一个新文件；已有快照直接复用，冻结命令拒绝覆盖。以下路径是本机已存在的材料目录，不是自动下载指令：

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.public_paper_fixtures --packet-dir workspace/acceptance/public-paper-packet-20261018 --output-file workspace/acceptance/public-paper-packet-20261018/frozen-public-papers-v1.json
.\.venv\Scripts\python.exe -X utf8 -m scripts.eval_workspace --scenario public-quality --source-packet workspace/acceptance/public-paper-packet-20261018/frozen-public-papers-v1.json
~~~

场景通过本机 HTTP 发送四轮自然语言：定位历史论文、方法/实验比较、证据与费用推断边界、两句精简追问。模型仍自主选择工具，不强制读取篇数、顺序或格式。`--source-packet` 仅允许 public-quality；本场景拒绝 `--live-search` 和程序入口的 search_mode=live，材料在整轮中冻结。

默认 mock 只证明管道，可能没有工具动作。需要真实模型时继续沿用现有显式 --live、调用开关、当前额度确认及共享请求/token保护；本轮未执行。离线 semantic_review 为 not_applicable_offline_fixture，真实模型为 pending，均不自动给研究质量评分。

每次使用全新输出目录和数据库；source-snapshot.json 保存规范化材料及字节哈希，human-facts.json 是待独立人工标注的空模板，不进入模型载荷。冻结全文仅留在验收产物中，模型通过工具收到有界片段；不把全文快照一次性塞入上下文。

## 本轮验证与剩余边界

修改前现有 4 项质量 HTTP 测试通过。新增 9 项测试覆盖正文按需交付、时间过滤、坏哈希/额外答案字段拒绝、来源范围、网络隔离、已有用户库不变、摘要回退与无匹配/空结果/重复调用统计，并通过实际本机 HTTP 场景复核摘要回退不会计作正文。

本机已有 MemGPT v2 与 Generative Agents v2 的冻结材料已通过脚本模型四轮 HTTP 验收，产物位于被 Git 忽略的 `workspace/acceptance/public-quality-cached-20261018-v1/`。两篇正文按需交付、无遗留运行，source-snapshot.json 哈希为 `0e320137e636dfb92e6bc76a05170463c6006200936b803b5fb5cb451f7e331c`。socket 连接限制为回环地址，外网和云模型请求均为 0；这是宿主管道证据，不证明自主研究质量。

首轮全量运行443项（90.884秒），其中1项已有 Web HTTP 测试出现 WinError 10053（本机连接被中止）。相关18项 Web 测试单独运行通过，同一出错测试随后连续5次通过；没有证据确定根因，不修改服务端、不增加请求重试。失败摘要保存在隔离验收目录的 offline-regression-initial.json，原始工具输出保留。同一代码集合全量复核443项通过（96.619秒），14项前端、Python/前端语法和依赖检查通过，发布预检323文件通过；没有跳过失败测试。复核完整输出保存在 offline-regression-recheck.txt。间歇性HTTP连接中止根因仍未确定，不因复核通过宣布该问题已修复。

本轮没有修改生产对话策略、默认模型、.env、真实用户库或模型账本。后续用当前有效授权和预算对这批公开材料做真实模型验收，人工比较实际正文交付与回答；Q05/Q06/Q07 和最新发现/发表核验仍保留为待验。
