# 论文引用影响力

资料库 → 论文资料 → 打开论文 → 查询指标。打开页面、收藏论文、启动服务均不会自动查询。支持 HTTPS arXiv 与 DOI 链接；其他资料链接仍能保存，但不提供引用查询按钮。也可对 lolo 说：“查询这篇论文的引用数和归一化引用影响力”，并给出链接或引用对应资料卡。

## 数据与解释

- Semantic Scholar：`citationCount`、`influentialCitationCount`。后者是该服务识别的重要引用，不是人工审定的论文等级。
- OpenAlex：`cited_by_count`、`fwci`、`citation_normalized_percentile.value`。FWCI 按同年、同类型、同子领域归一化，均值为 1；百分位值在 0–1 之间，界面按百分数展示。
- 两家分别保留记录链接、成功查询时间和最近尝试状态，不将计数相加。缺失值为未知，明确返回的 0 仍显示为 0。
- “影响力尚待观察”采用近 180 天的本地观察规则，不是服务商评级。日期取已确认发表日期与 arXiv 首次提交月份中的较早值；arXiv 月份按月初近似，不用修订日期把旧论文当新作。
- 引用影响不等于研究质量、项目适用性或阅读价值，不生成综合质量分，不更改论文原有优先级或个人记忆。

## 匹配、缓存与失败

以 DOI 或 arXiv ID 确认身份。Semantic Scholar 返回 DOI 时用于衔接 OpenAlex；否则最多用题名查 5 条候选，只有候选 locations 中明确含同一 arXiv 链接时才采用。仅题名相似、多个匹配、标识冲突都不会自动套用数据。

成功结果缓存 24 小时，失败结果默认 10 分钟后可再次按需查询；手动刷新间隔至少 1 分钟。不自动重试，不后台轮询。刷新失败保留最后成功数据及原始时间，明确标为待更新。HTTP 429 显示接口限流，401/403 显示凭据状态，未找到显示未收录；均不等同零引用。外部请求只访问固定学术接口，不跟随重定向；联网等待不占全局应用锁。

缓存位于 `paper_impact_cache`，与资料库共用数据库。引用论文到聊天时会附带已有指标、来源、时间及缺失说明；聊天工具 `get_paper_impact` 也使用同一缓存，并受本轮工具预算和禁止外部检索策略约束。不会改变原文阅读深度。

## 可选凭据

默认尝试无需密钥的基础接口。可在被 Git 忽略的 `.env.local` 配置以下服务端环境变量并重启：

```dotenv
SEMANTIC_SCHOLAR_API_KEY=你的SemanticScholar密钥
OPENALEX_API_KEY=你的OpenAlex密钥
```

不在网页表单、模型上下文、缓存或错误消息中保存密钥。不自动购买额度或创建账户；共享接口限流时可稍后再试，或自行申请官方密钥。

官方资料：[Semantic Scholar API](https://api.semanticscholar.org/api-docs/graph)、[OpenAlex 指标定义](https://help.openalex.org/data/works/attributes/)、[OpenAlex 认证与限流](https://help.openalex.org/api/authentication/)。
