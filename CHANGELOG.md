# Changelog

## Unreleased · V0 reliability and quality work — 2026-10-17

### Fixed

- Extend the existing Web learning tool with correction/revocation by evidence ID. Reuse memory-center mutations, attach the actual user message, downgrade corrections to self-report, retire stale context and preserve concurrent-run protection. No new tool or intent route.

- Load the Anthropic SDK only for its provider; mock and DashScope startup avoid unrelated SDK/trust-store imports. Windows experiment receipts collect local runtime facts without WMI and disclose that the architecture is the Python build target.
- Disable SDK retries and remove implicit HTTP/empty-answer resubmission, token-budget growth and reasoning fallback. Keep the existing, explicit one-time invalid-JSON repair separate. Preserve safe HTTP classification without including upstream response bodies in error messages.
- Release failed task startup slots, persist a safe terminal record and preserve request-key idempotency. An acknowledged cancellation takes precedence over startup failure; actual created-but-not-submitted task receipts remain distinct.
- Recognize data-only SSE errors across LF, CRLF, CR and case-insensitive MIME headers while retaining preceding text and successful bytes.
- Close an acceptance server socket if its worker cannot start; clean up smoke-test temporary directories after failures as well as successes.
- Write fixed-source snapshots as exact UTF-8 bytes and verify the stored bytes, avoiding Windows newline conversion/hash mismatch. Preserve earlier mismatched evidence rather than rewriting it.
- Let read_task wait briefly for a selected active task with cancellation and an explicit deadline, avoiding repeated model calls just to wait. This remains pending a real-model retest after the authorized batch was exhausted.

### Added

- Optional DashScope-only token reservations in isolated acceptance, alongside the shared physical-request cap. Pre-reserve UTF-8 request bytes, output/reasoning allowances and overhead; refund only coherent usage from completed calls. Errors and unknown usage retain reservations, and uncertain/excess usage stops further requests. This estimates local consumption, not the cloud balance or billing ceiling.

- Shared physical inference-attempt limits for Anthropic and DashScope acceptance clients, including independent transports for foreground/background clients. This is a local limit, not a cloud free-quota guarantee.
- Frozen QF-1 synthetic sources and a four-turn `--scenario quality` HTTP acceptance path for source discovery, method/experiment comparison, publication/numeric claims and concise followups. Store source snapshots/hashes separately from human-only facts; never feed reference answers to the model or claim a semantic pass automatically.
- A staged V0 development plan and eight human review cards. Updated old development conventions to reflect the current model-led dialogue rather than the legacy fixed research loop.

### Validation

- Latest frozen code: 405 offline Python regressions and 14 frontend tests passed, with no WMI workaround or skipped tests. Syntax, dependency and release checks passed; test resources were cleaned up. The earlier 384-test snapshot remains in the historical review.
- Public arXiv retrieval and one query-selected PDF read succeeded. The 24 authorized real-model requests do not establish semantic acceptance; failed answers, budget stops and older hash-mismatch artifacts are retained.

### Scope

- After explicit free-quota/free-only confirmation, 24 isolated qwen3.8-flash requests were used across fixed-evidence and two-task scenarios. All returned HTTP 200; both batches stopped at their allocated limits. Three research answers still failed manual semantic review; two child tasks completed but parent polling exhausted the summary/memory budget. No default-model changes, paid search, user-database migration, automatic experiment execution or public deployment. The prior WMI/service-stream root causes remain unresolved.


## [0.5.0] - 2026-10-11 · V0 local release candidate

### Added

- Persistent main conversation, task center and model-led background task delegation. Tasks retain separate chats, progress, sources and model settings while sharing one user identity.
- Personal/project/task memory scopes; project registration and binding; editable, disableable and forgettable facts with revisions and provenance.
- Technology/method learning evidence inspection, correction and revocation. Foreground learning tools attach actual user-message evidence; background work cannot write personal facts or user learning events.
- One-time migration of existing Web sessions and notes, preserving IDs and transcripts. Old task notes remain task-scoped.
- Minimal white monochrome Web UI, streaming, run events, idempotent submission, cancellation and interrupted-run recovery.
- Optional official Alibaba WebSearch MCP adapter and persistent reservations against operator-verified free allowance. Unverified allowance never enables automatic paid fallback.
- GitHub regression workflow and V0 readiness documentation.

### Changed

- Kept dialogue model-led with a shared operation budget and a reserved final answer; added scoped host tools without a fixed intent classifier or conversation state machine.
- Bounded automatic fact recall to 20 records / 8000 characters, with query-aware selection and on-demand memory reads.
- Memory correction/disable/forget retires affected old model history and cached evidence while preserving visible transcripts. This conservative cutoff reduces available prior context.
- Per-model reasoning controls replace the Web runtime preset selector; saved legacy profiles remain readable.
- Alibaba model discovery uses the official paginated directory. In-flight configuration stays frozen, while saved choices immediately update the UI.

### Fixed

- Removed forgotten content from both live tool dialogue and the reserved answer context.
- Prevented background tasks from bypassing personal-memory restrictions by supplying another scope.
- Separated answer citations from unused retrieval records, preserved distinct arXiv versions and bounded historical provenance to earlier messages.
- Preserved safe terminal errors after refresh without exposing provider exception bodies.
- Made the executor-resolution smoke test independent of locally installed coding CLIs; it now covers both available choices and the missing-CLI error.
- Avoided unchanged task-list rebuilds and removed hidden mobile navigation from keyboard/accessibility navigation.
- Reported public search challenge/transport failures explicitly and removed an irrelevant Bing RSS fallback.

### Validation and release scope

- 311 offline Python regressions and 11 frontend source tests passed; JavaScript syntax and dependency checks passed.
- Browser acceptance covered two delegated tasks, separate results, shared memory edits, learning correction and desktop/mobile layouts. These tests used a synthetic offline model.
- Database backup-copy migration preserved 3 spaces, 8 messages and the original note; SQLite integrity check passed.
- A 0.5.0 wheel was built successfully. Public arXiv retrieval returned 2 papers with complete abstracts.
- Public web search still reports provider_unavailable. Real-model/free-quota acceptance and public multi-user deployment remain unverified.
- The previous orange version is retained in codex/legacy-orange-v0. Current V0 is promoted to master without rewriting history.
- Details, remaining launch blockers and rollback instructions: docs/v0-release-check.md.

## Harness update - 2026-10-09

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
