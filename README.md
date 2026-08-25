# Lodestar - Agent Research Lab

<p align="center">
  <strong>A task-driven research agent that turns frontier signals into project-grounded, auditable experiments.</strong>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ?
  <a href="#how-it-works">How it works</a> ?
  <a href="#architecture">Architecture</a> ?
  <a href="#deployment">Deployment</a> ?
  <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Demo replay" src="https://img.shields.io/badge/Demo-Curated%20Replay-F28C28">
  <img alt="Tests" src="https://img.shields.io/badge/Tests-12%20offline%20checks-2EA44F">
  <img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-4A5568">
</p>

> **Lodestar** is a portfolio project about the product thinking behind reliable agents: retrieval is only useful when it is traceable, memory is only useful when it is trusted, and an insight is only useful when it can become a testable experiment.

## Why Lodestar?

Most research assistants stop at a polished answer. Lodestar is designed around the steps that make an answer useful in a real product:

1. identify what changed in the field;
2. explain the concept instead of only listing a paper;
3. connect the signal to the active project's code and implementation gap;
4. preserve sources and tool decisions as an auditable trace;
5. ask the user before changing long-term memory;
6. turn a confirmed insight into an executable experiment scaffold.

This makes Lodestar a compact demonstration of agent memory, tool calling, human-in-the-loop control, project-grounded retrieval, evaluation, and the research-to-build loop.

## The demo in one minute

The default portfolio mode is a deterministic **Curated Demo Replay**. It does not require an API token, network access, or a live model, so an interviewer can repeat the same flow without watching a loading spinner fail:

```text
Weekly frontier scan
        |
User selects a research direction
        |
Paper and PDF evidence plus project-code relevance
        |
Auditable Research Trace
        |
User confirms a Knowledge State update
        |
Confirmed insight -> Experiment scaffold
```

The local UI is available at `http://127.0.0.1:8123`. A deployed replay is also supported through the Vercel adapter in [`api/index.py`](api/index.py).

## What the current build demonstrates

| Capability | What to look for in the product | Implementation surface |
| --- | --- | --- |
| Frontier research | Three signals with concept, paper finding, Lodestar relation, and PDF links | `lodestar/frontier.py`, `lodestar/demo.py` |
| Tool calling | Search, read, project-context, knowledge, and registry tools | `lodestar/tools/`, `lodestar/mcp_server.py` |
| Research Trace | Ordered events for planning, retrieval, evidence assessment, and synthesis | `lodestar/trace/`, `research_tasks.trace_events` |
| Project relevance | Explainable relevance score plus matching repository files | `lodestar/relevance.py`, `lodestar/project_index.py` |
| Memory lifecycle | Pending proposal -> user confirmation -> Knowledge State update | `lodestar/memory/repo.py`, `/api/conversation/:id/remember` |
| Evaluation | Offline golden cases for coverage, faithfulness, sources, and task success | `tests/`, `lodestar/eval/` |
| Experiment loop | Research opportunity -> baseline/candidate scaffold -> `eval.py` | `lodestar/experiment.py`, `workspace/experiments/` |
| Stable presentation | Curated replay, paced streaming, fixed demo dataset, no token required | `lodestar/demo.py`, `api/index.py` |

## How it works

### 1. Research state, not just chat history

Every run has a task record, sources, trace events, Knowledge State proposals, and optional experiment artifacts. The conversation is the surface; the state underneath is the product.

### 2. Memory is a governed write path

Research can propose a memory update, but it cannot silently rewrite the user's long-term context. The UI exposes the proposal, the user chooses what to retain, and the repository records the applied update. This is the product boundary between **retrieval** and **memory**.

### 3. Project relevance is explainable

A weekly signal receives an explainable score built from technology-stack overlap, project context, code evidence, and active status. The result is not "this feels relevant"; it is a concrete path from:

```text
paper signal -> project evidence -> implementation gap -> experiment hypothesis
```

### 4. Trace is a first-class artifact

The agent records the path it took, not only the final markdown. That makes it possible to answer: which source was used, which tool was called, which code files were matched, why a memory update was proposed, and what should be evaluated next.

## Architecture

```mermaid
flowchart LR
    U[User question] --> UI[Lodestar UI]
    UI --> API[HTTP API / SSE]
    API --> LOOP[Research Loop]
    LOOP --> PLAN[Planner]
    LOOP --> TOOLS[Tool Registry]
    TOOLS --> PAPERS[Paper + web search]
    TOOLS --> PROJECT[Project index]
    TOOLS --> KNOWLEDGE[Knowledge State]
    LOOP --> TRACE[Research Trace]
    LOOP --> BRIEF[Research Brief]
    BRIEF --> HITL[Human confirmation]
    HITL --> MEMORY[Memory update]
    MEMORY --> EXP[Experiment scaffold]
    EXP --> EVAL[Offline evaluation]
```

The system has two execution paths:

- **Curated replay**: fixed, source-backed demo data for a reliable portfolio presentation.
- **Research loop**: planner -> tools -> reranker -> assessor -> synthesizer, with live or mock providers selected by configuration.

The UI keeps both paths behind the same task and evidence model, so the demo communicates the real architecture without requiring a live provider at every step.

## Quickstart

### Requirements

- Python 3.10+
- Windows, macOS, or Linux
- No token required for the offline demo

### Install

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
.\\.venv\\Scripts\\Activate.ps1

python -m pip install -r requirements.txt
```

### Run the deterministic demo

```powershell
$env:LODESTAR_DEMO_REPLAY="true"
python -m lodestar demo reset
python -m lodestar ui --port 8123 --no-browser
```

Open <http://127.0.0.1:8123> and send the pre-filled frontier question. The demo is intentionally repeatable and does not consume model tokens.

### Verify the environment

```powershell
Invoke-RestMethod http://127.0.0.1:8123/api/demo/readiness | ConvertTo-Json -Depth 6
```

```bash
python -m unittest tests.test_smoke -v
python scripts/preflight_public_release.py
```

The public-release preflight is dependency-free. It scans the release set for common token formats, private keys, oversized files, and accidentally tracked runtime artifacts.

## Live research mode

The replay is the recommended first run. To connect a compatible Anthropic endpoint for real research, copy the safe template and configure credentials locally:

```powershell
Copy-Item .env.example .env
```

Then set the required provider variables in `.env` or your shell. `.env` is ignored and must never be committed. Live mode also benefits from a persistent database and workspace directory; the Vercel deployment intentionally stays in replay mode.

## Deployment

### Vercel: portfolio replay

The repository includes a Vercel Python entrypoint and routing file:

- [`api/index.py`](api/index.py) adapts the existing HTTP handler;
- [`vercel.json`](vercel.json) routes the UI and API through that function;
- [`.vercelignore`](.vercelignore) keeps local databases, experiments, and caches out of the deployment.

Deploy with the Vercel CLI:

```bash
npm install -g vercel
vercel login
vercel link
vercel --prod
```

The adapter sets `LODESTAR_DEMO_REPLAY=true`, uses `/tmp` for ephemeral state, and completes replay tasks synchronously so serverless freezing cannot interrupt the demo. This is a deliberate product decision: **Vercel is the stable showcase surface; local mode is the development and live-research surface.**

### Local production-like check

```bash
python -m py_compile lodestar/ui.py api/index.py
python scripts/preflight_public_release.py
```

## Repository map

```text
Lodestar/
|-- api/index.py              # Vercel serverless entrypoint (demo replay)
|-- docs/                     # design notes, demo script, recording runbook
|-- lodestar/
|   |-- agent/                # planner, retrieval loop, assessor, synthesizer
|   |-- build/                # coding-agent executors and scaffold execution
|   |-- eval/                 # golden cases, metrics, evaluation harness
|   |-- harness/              # Codex conversation harness integration
|   |-- memory/               # SQLite schema and Knowledge State repository
|   |-- tools/                # paper, web, project, knowledge, registry tools
|   |-- trace/                # ordered Research Trace recorder
|   |-- demo.py               # curated dataset and deterministic replay
|   |-- experiment.py         # opportunity extraction and experiment scaffolds
|   |-- project_index.py      # local/GitHub project indexing
|   |-- relevance.py          # explainable project relevance scoring
|   `-- ui.py                 # zero-build local UI, HTTP API, and SSE stream
|-- scripts/
|   `-- preflight_public_release.py
|-- tests/                    # offline smoke, lifecycle, harness, and project tests
|-- .env.example              # safe configuration template; no credentials
|-- vercel.json               # Vercel routing/build configuration
|-- pyproject.toml            # package metadata and CLI entrypoint
`-- requirements.txt          # runtime dependencies
```

Runtime directories are intentionally ignored:

- `lodestar/data/` - local SQLite database;
- `workspace/` - briefs, traces, source snapshots, and experiment output;
- `experiments/` - generated experiment projects;
- `.env` - local credentials and provider configuration.

## Useful commands

```bash
python -m lodestar research "<goal>" --mock --offline --yes
python -m lodestar eval --mock --offline

python -m lodestar knowledge list
python -m lodestar knowledge search memory
python -m lodestar knowledge diff <task_id>

python -m lodestar experiment list
python -m lodestar experiment save <task_id>
python -m lodestar experiment build <exp_id> --scaffold-only

python -m lodestar project add https://github.com/owner/repo --status active
python -m lodestar project list
python -m lodestar project index <id> --path <local-repo>
```

## Demo recording flow

The recommended portfolio story is intentionally short:

1. Ask: "What new agent research should Lodestar prioritize this week?"
2. Show three signals: concept, paper finding, project relation, and PDF source.
3. Choose the trusted-memory signal and ask why relevant memories can still hurt reasoning.
4. Expand the project evidence and show matched files plus the relevance score.
5. Confirm the memory update to write the Knowledge State.
6. Continue to the related code, then generate an experiment scaffold.
7. Open the experiment project and show `baseline.py`, `candidate.py`, and `eval.py`.

The full recording script is in [`docs/demo-recording-v2.md`](docs/demo-recording-v2.md).

## Evaluation and engineering trade-offs

Lodestar ships with offline golden cases so the core workflow can be tested without a provider or network. The suite checks source uniqueness, evidence coverage, venue metadata, faithfulness, task success, Knowledge State behavior, project relevance, and experiment scaffolding.

The current implementation intentionally favors:

- deterministic replay over a fragile live demo;
- explicit user consent over silent memory writes;
- explainable relevance over a single opaque similarity score;
- small composable tools over one giant research function;
- artifacts and traces over a final answer that cannot be inspected.

## Security and public-repository policy

Before publishing, run `python scripts/preflight_public_release.py` and inspect `git diff --cached`.

Never commit:

- `.env`, API keys, access tokens, cookies, or private keys;
- SQLite databases and generated traces;
- PDF caches, screenshots, recordings, or local experiment outputs;
- local proxy URLs, account identifiers, or machine-specific paths.

The repository is safe to run publicly in replay mode. Live provider credentials belong in Vercel environment variables or a local `.env`, never in source code.

## Roadmap

- [x] Evidence-backed weekly frontier scan
- [x] Project-code relevance mapping with explainable score
- [x] Research Trace and SSE streaming
- [x] Human-confirmed Knowledge State updates
- [x] Offline evaluation and experiment scaffold loop
- [x] Stable Vercel demo adapter
- [ ] Persistent hosted memory with a managed database
- [ ] Source-level claim verification and citation diffing
- [ ] Evaluation dashboard for memory traps, tool-use recovery, and token cost
- [ ] Multi-project workspace and permissioned memory scopes

## License

Apache-2.0. See [`LICENSE`](LICENSE).

## Acknowledgements

Lodestar is a learning-oriented implementation built to make agent product decisions inspectable: what the system remembers, which tools it calls, how it uses evidence, and how a research idea becomes a measurable build task.
