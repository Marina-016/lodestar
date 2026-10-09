# arXiv 排查（2026-10-09）

本轮零模型调用，仅 DNS、代理、少量 HTTP 对照与代码审查。不更改系统代理或项目代码。

| 路径/请求 | 结果 | 耗时 |
| --- | --- | --- |
| 当前默认路径：API all:electron，1条 | 200 Atom | 0.91s |
| arxiv.org 首页 | 200 HTML | 0.88s |
| Attention Is All You Need 摘要页 | 200 HTML | 1.44s |
| 原失败查询 abs:artificial intelligence recent papers，按提交日期 | 200 Atom，4条 | 2.34s |
| cat:cs.AI，按提交日期 | 200 Atom，4条 | 1.74s |
| abs:"large language model" AND abs:reasoning | 200 Atom，4条 | 3.95s |
| 单次禁用系统代理的 all:electron 请求 | 200 Atom | 1.75s |

查询诊断至少间隔3秒，遇429即停止的保护未触发。200不证明后续长期稳定，也不否定前轮真实超时/429。

## 已确认

环境代理变量未配置，但 requests 使用 Windows 系统代理 127.0.0.1:7890。
默认代理路径与单次直连均成功，不能据此认定代理损坏、被封IP或中国网络阻断。
DNS可解析。原始复杂请求本次 X-Cache 包含 MISS，仍成功；并非所有成功仅由缓存产生。

搜索、发现、摘要读取分别发起API请求，没有统一的3秒请求间隔/单连接门控、429冷却或搜索元数据缓存。
PDF已有缓存，不等于搜索API有缓存。之前测试模型在服务故障后重复改词调用，扩大耗时和请求量；
缺少请求时间线、当时响应体与出口信息，不能证明429唯一由本项目频率触发。

原查询在响应feed标题中被解析为：
abs:artificial OR all:intelligence OR all:recent OR all:papers
这是已确认的查询表达缺陷，会造成宽泛与无关结果；不能从一次对照认定它是超时的直接原因。
字段、短语、AND/OR与时间区间应明确表达，年份不是日期过滤。

## 建议修复顺序

1. 统一arXiv客户端，覆盖搜索、发现、摘要读取入口；本机多进程协调3秒间隔、单请求在途。
2. 429遵循Retry-After；缺失时做有限冷却并交回模型选择其他来源，不连续改词重试。
3. 缓存成功的论文元数据和查询结果，标注抓取时间；缓存故障不伪装成空结果。
4. 让模型选择关键词、领域、时间范围与排序，工具明确编译arXiv查询。不要用自然语言关键词路由替代模型。
5. 接通已有discover_papers和HF平台热门入口；HF热度不等于全网热门，arXiv RSS也不是独立于arXiv的可靠性来源。
6. 记录连接/读取超时、HTTP状态、耗时、服务端Retry-After及查询；稳定后再真实跑用户问法。

仅提高15秒超时不能解决429、错误查询或缺少缓存。

官方依据：
- https://info.arxiv.org/help/api/tou.html ：legacy APIs 每三秒最多一次、一次一个连接，要求按所有受控机器整体遵守。
- https://info.arxiv.org/help/api/user-manual.html ：查询字段、短语、布尔运算和submittedDate范围。
