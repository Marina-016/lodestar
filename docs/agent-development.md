# Headless Agent development

This iteration reuses the deterministic research loop, tool registry, SQLite
and Trace. No UI changes, background jobs or experiment execution were added.

## Run

From the repository root, use `.venv/Scripts/python.exe` on Windows.

```powershell
.\.venv\Scripts\python.exe -m lodestar research "Agent memory 有什么新进展" --recent-days 7
.\.venv\Scripts\python.exe -m lodestar research "Agent memory 有什么新进展" --recent-days 7 --mock --offline
.\.venv\Scripts\python.exe -m lodestar learning list --technology Harness
.\.venv\Scripts\python.exe -m lodestar learning record --technology Harness --method "Tool routing" --event discussed --evidence "用户讨论记录或任务引用" --paper-url "https://arxiv.org/abs/PAPER_ID"
.\.venv\Scripts\python.exe -m lodestar learning revoke --evidence-id 1
```

Live synthesis requires `ANTHROPIC_API_KEY` and a model supported by that account
(configure `LODESTAR_MODEL`). Store credentials in an ignored local `.env` or
the process environment. Never put them in source files. `--mock --offline`
explicitly uses fixtures and does not verify live model generation.

`--project-id ID` binds a registered project and generates a proposal using its
indexed documents. Register/index the project with the existing `project`
commands first. Proposals remain drafts; no code is changed or executed.
For full-text reading set `LODESTAR_FULL_TEXT=true`; full-text is bounded by the
existing read budget. Missing full text is explicitly reported in proposals.

## Contracts

- `discover_papers`: recent arXiv submissions in a UTC rolling window, or a
  bounded HF trending list filtered by query terms. It is not exhaustive or a
  claim about worldwide popularity. Empty success and provider failure differ.
- Versioned arXiv IDs deduplicate by paper identity; submission, revision and
  retrieval timestamps remain distinct in discovery/Trace payloads.
- `read_learning_profile` / `record_learning_evidence`: user-specific observations
  about a technology and optional method. No stored concept encyclopedia.
- `explained` observations require source and explanation substring evidence
  when produced automatically. They record delivered explanation, not proof the
  user read it. They never establish mastery.
- Self reports are retained as self reports. Demonstrated explanation,
  implementation and application require user evidence; agent execution is
  rejected as user mastery evidence. Evidence can be revoked and is retained in
  the audit history. The read API returns a bounded history, not an exhaustive
  lifetime assessment.
- Research knowledge proposals remain pending in unattended runs. Explicit
  `--yes` retains its opt-in application behavior. Existing concept records are
  research knowledge and must not be treated as user mastery.
- Project proposals cite indexed paths and paper URLs. Missing evidence yields
  a draft. There is no automatic execution or adoption.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Real Alibaba qwen3.8-flash validation now covers paper retrieval/body excerpts,
persistent follow-up, acknowledgement without mastery promotion, and a grounded
structured proposal. Provider-free quota and stop-on-exhaustion were checked in
the user's console. See agent-mvp-runbook.md and the acceptance report for current
artifacts and limits. Offline fixtures remain distinct from live quality checks.
Experiment/A-B execution, scheduled proactive discovery, broader topic matching,
automatic mastery assessment and UI remain subsequent work.

Official API references:
- https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/user-manual.md
- https://huggingface.co/.well-known/openapi.json

学习画像以完整未撤销历史计算掌握状态与论文数量；只对返回的方法数、近期证据与论文 URL 数设限，避免新事件挤掉早期掌握证据。画像保留 mastery_evidence 供追溯，撤销该证据会重新计算状态。
