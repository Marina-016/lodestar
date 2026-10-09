# Changelog

## [Unreleased] - 2026-10-09

### Changed

- Simplified ordinary dialogue to model-led tool selection and direct Markdown answers; removed forced answer blocks, quote gates and title-only rendering. Structured memory and experiment safeguards remain separate.
- Passed complete paper abstracts to the model and preserved candidate context for consecutive questions.

### Fixed

- Added shared arXiv request spacing, metadata caching and failure cooldowns; applied recent-date filters and normalized search terms.
- Preserved Chinese paper explanations instead of replacing them with links; corrected provider JSON-mode handling for structured calls.

### Validation

- 189 regression tests passed.
- Live three-turn acceptance passed: recent AI papers, explanation of the second paper, and a three-sentence rewrite without network tools. This checks the interaction flow, not full factual correctness.
- Local credentials, databases and runtime logs remain excluded from Git.

## Backend MVP - 2026-10-08

### Added

- Added a headless research/conversation service with persistent user/project sessions, recent arXiv and HF trending discovery, bounded paper evidence and failure recovery.
- Added project paper subscriptions, due polling leases, per-project/version-aware candidate deduplication, reading history and evidence handoff.
- Added scoped applicability assessment and evidence-bound draft actions: `propose_change`, `investigate`, and `no_change`.
- Added reviewed Python paired execution, independent exact-output grading, frozen artifacts, integrity checks, environment receipts and result lineage.
- Added an isolated context-policy diagnostic with source-bound working notes, saved model turns and candidate investigation registration.
- Added independent zero-API `agent-demo` and `agent-pipeline-demo` CLI demonstrations.

### Changed

- Separated research knowledge from user technology/method learning evidence. Explanations and self-reports do not upgrade mastery; revocation recomputes valid history.
- Grounded followups in quoted paper claims, with separate hypotheses, missing evidence and general background. Only supported delivered paper methods are eligible for exposure recording.
- Restricted remote project context to explicitly authorized repository/file excerpts, with model-disable gates and no project metadata export through this path.
- Required reassessment when candidate reading or bound project evidence changes; mock assessments cannot become live proposal evidence.
- Improved Chinese project queries by splitting technical identifiers only when the original lexical query has no matches. User-facing assessment/proposal prose is requested in Chinese while source quotes remain verbatim.

### Fixed

- Prevented older paper versions and failed newer reads from masquerading as current evidence.
- Preserved delivered answers when exposure extraction fails, keeping audit metadata out of future conversation messages.
- Attached bounded source provenance and rejected unmatched/stitched followup quotes, allowing whitespace-only PDF restoration.
- Stopped experiments with mutated artifacts or invalid outputs from receiving conclusive success labels.

### Validation and limits

- 147 regression tests passed; 12 CLI acceptance commands included actual paired execution and result retrieval.
- Real public discovery, bounded body reads, model assessment/proposal and context diagnostics were exercised. Negative results and rejected drafts are retained.
- Diagnostics use known synthetic cases; they do not establish project gains, full paper replication or user mastery. Adoption remains separate.
- Frontend redesign, permanent background notifications and automatic mastery grading remain future work. Secrets and local runtime artifacts are excluded from Git.

### Documentation

- Consolidated the PRD, architecture, runbook, experiment/reproduction boundaries and requirement-by-requirement acceptance audit.
- Updated README with current backend capabilities, safe isolated demos and explicit separation from the historical hosted UI replay.

## 历史开发记录 - 2026-08-24

### Added

- Rebuilt the curated replay around trusted Agent memory using the latest MemTrapBench, CAMA, cross-task Skill transfer, Skill selection and MidTool papers.
- Added a project-grounded memory risk assessment event to the auditable Agent trajectory.
- Added trusted-memory experiment cases for misleading relevant memory, correlated false majorities, stale conflicts and useful independent memory.
- Added a clean reset workflow that backs up the current database before rebuilding recording data.

### Documentation

- Consolidated recording instructions into `docs/demo-recording-v2.md` and `docs/demo-recording-runbook.md`.
- Documented the exact two-prompt workflow, narration, truthfulness boundaries and reset procedure.

### Changed

- Replaced the previous AMR/Eureka/SkillGate demonstration narrative with a single coherent flow from weekly research to Memory Trust Gate.
- Updated offline Agent Memory and Frontier sample responses to match the trusted-memory research question.
- Added SSE streaming task snapshots and incremental brief rendering in the local UI; localized non-essential interface labels to Chinese.

All notable changes to Lodestar are documented here.

## [0.4.7] - 2026-08-20

### Added

- Research Desk visual system with light/dark theme switching.
- Research trace rail for question → source → evidence → insight → next move.
- Loading skeletons and lightweight page/card motion with reduced-motion support.
- Curated demo history filter and inline demo labels.
- Localized history and experiment timestamps in Asia/Shanghai time.

### Fixed

- Fixed raw UTC ISO timestamps being shown in the research history page.
- Fixed history records failing to open their research detail after the motion pass.
- Improved light-theme contrast for primary actions, statistics, and status cards.

### Verified

- python -m unittest tests.test_smoke -v — 10 tests passed.
- Local preview verified at http://127.0.0.1:8123/.
- Demo flow verified from history record to research trace, Next Move, Trace, and Save Experiment.
