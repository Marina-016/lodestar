# Lodestar 开发约定

## 项目定位
Single Agent + Tools 的个人 AI 前沿研究 Agent（V0：Research → Learn → Knowledge）。
Agent 名 **Lodestar（导星）**，常量在 `lodestar/__init__.py`，prompts/README 引用它。
原则（源自 PRD §26）：
1. 不做论文摘要器，做长期 Research Agent。
2. 不做万能 Agent，先把一个 Research Loop 做深。
3. 不强行 Agent 化——确定性步骤用 Code（去重/截断/限流/预算/FTS）。
4. Memory 的价值是减少重复、提供个性化 Context。
5. 每加一个 Agent 能力，必须有对应 Eval。

## 关键决策
- **编排器是确定性代码**（`agent/loop.py`），LLM 只产内容（Plan/Query/Rerank/Assess/Synthesis/Novelty/Judge），不决定工具图。不要引入 LangGraph/LangChain。
- **Tools**：`tools/registry.py` 注册，`call_tool` 统一捕获异常返回 `{"error":...}`，绝不让单工具崩溃打断 Loop。
- **Mock 双通道**：`llm_mode=mock`（LLM 夹具）+ `search_mode=mock`（检索/读取夹具）→ 冒烟测试全离线、确定性。新增 LLM role 时必须同步 `llm.py::MockLLM`；新增工具 fixture 时同步 `fixtures.py`。
- **Trace 先于执行**：loop 每一步先 `trace.log` 再执行（`trace/recorder.py`）。
- **Knowledge 写入只走 pending proposal**（`update_knowledge_proposal` 工具 → 确认 → applied），HITL 见 `agent/loop.py::_decide_updates`。
- **Eval 两层**：确定性指标（Trace 算）为真值底座；质量指标由 Judge-LLM 打（judge 失败 → `None` → verdict=warn，不编造）。

## 数据流
`cli.py` → `agent/loop.py::ResearchAgent.run()` → tools → memory/repo + trace → brief 落 `workspace/<task_id>/` → eval 用隔离库 `data/eval_lodestar.db`（不污染真实 Knowledge State）。

## 测试
```bash
python -m unittest tests.test_smoke -v
```
回归纪律：改 Prompt/Agent/Tool 前先跑一次基线，改完对比；golden case 阈值在 `lodestar/eval/cases/*.json` 的 `thresholds`。

## 环境
- Python 3.12；依赖仅 4 个（`requirements.txt`）。
- 不在仓库写 Key；`.env` 不入库。
- 语言：外部技术术语保留英文，叙述默认中文（`LODESTAR_BRIEF_LANGUAGE` 可切 en）。
