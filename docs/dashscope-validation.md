> 本文是早期 qwen-flash 历史验收。当前免费模型结果和已知限制见 [MVP 验收记录](agent-mvp-acceptance.md)。

# 阿里低成本模型运行记录

采用 qwen-flash，关闭思考，不自动切换昂贵模型。模型接口通过 LODESTAR_LLM_PROVIDER=dashscope、LODESTAR_LLM_BASE_URL、DASHSCOPE_API_KEY 配置。研究与 judge 模型均可使用 qwen-flash。

真实公开论文运行产物位于 workspace/live-flash-public（Git 忽略）。三轮包括论文研究、追问和自评，8 次模型调用，输入 18039 tokens，输出 6207 tokens，按北京 <=128k 标准原价估算约 0.012016 元。优惠、缓存和实际账单可能不同。

61 项离线测试通过。真实运行只证明流程与接口可用：只深读一篇 Raven；候选包括旧稿，不能都称近期论文；多论文覆盖、结论语义支持和方案质量尚未通过验收。

本轮密钥只注入运行进程，没有写入源码或报告，也未持久化到 .env。后续本地运行需设置 DASHSCOPE_API_KEY，不要提交到 Git。

项目方案运行被自动审批拒绝：未明确授权把本地项目代码发送到阿里端点。公开论文运行未加载项目索引。用户随后明确授权三个指定文件片段，项目方案已单独完成调用；质量复核未通过，见 qwen-flash-quality-review.md。

价格依据：https://help.aliyun.com/zh/model-studio/qwen-flash