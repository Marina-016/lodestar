# Changelog

## Unreleased · V0 reliability and quality work — 2026-10-17

### Fixed

- Link task transcript messages to verified runs of that task, disclose message kinds and truncation, and retain both ends of long results. Keep wait/cancellation and context-retirement boundaries.
- Forward child source provenance without claiming the parent received original body text; preserve existing parent paper/web bodies and allow ordinary authorized reads when needed.

- Bound learning-profile JSON at model boundaries to 12000 characters, retaining source/mastery references and explicitly omitting evidence strings that do not fit. Never slice or summarize user evidence, change stored history or replace full memory-center data. Apply the same view to automatic context, memory reads, edit/reset results and legacy synthesis.

- Deduplicate learning recall with the same NOCASE identities as profiles and project matching methods directly, so 500 newer methods cannot hide an exact older match. Retain full-history mastery support and disclose omitted evidence.
- Report memory match counts, distinct learning-profile counts, filter fields and truncation independently from event counts or empty query results; preserve the shared read snapshot.

- Count observed body evidence using actual depth, returned text and query matches; disclose abstract fallbacks separately from full-text requests. Preserve incremental coverage before a failed run and exclude unchecked publication snapshots.

- Normalize numbered PDF headings and preserve repeated sections; include Approach and experimental variants in bounded section excerpts, retaining room for method/experiment evidence and reporting truncation.
- Honor queries on generic PDFs and key their caches by complete URL instead of filename. Keep delivered query spans and match flags aligned with the caller's character budget.

- Accept schema-valid redundant event values when correcting learning evidence, while always storing user self-report. Keep unsupported mastery events, revocation arguments and concurrent mutations protected.
- Distinguish lexical memory-search misses from an empty profile using scoped counts and bounded overview guidance; counts and returned data share one read snapshot.

- Normalize decoded acceptance JSON before UTF-8 token reservation so equivalent Unicode escaping does not consume different local allowance. Preserve unknown/error reservations and report only safe numeric denial details.
- Disclose observed paper reading depth and bounded coverage in canonical context, retaining abstract fallbacks and merged body excerpts. Clarify the existing full_text parameter without forcing a research route.

- Extend the existing Web learning tool with correction/revocation by evidence ID. Reuse memory-center mutations, attach the actual user message, downgrade corrections to self-report, retire stale context and preserve concurrent-run protection. No new tool or intent route.

- Load the Anthropic SDK only for its provider; mock and DashScope startup avoid unrelated SDK/trust-store imports. Windows experiment receipts collect local runtime facts without WMI and disclose that the architecture is the Python build target.
- Disable SDK retries and remove implicit HTTP/empty-answer resubmission, token-budget growth and reasoning fallback. Keep the existing, explicit one-time invalid-JSON repair separate. Preserve safe HTTP classification without including upstream response bodies in error messages.
- Release failed task startup slots, persist a safe terminal record and preserve request-key idempotency. An acknowledged cancellation takes precedence over startup failure; actual created-but-not-submitted task receipts remain distinct.
- Recognize data-only SSE errors across LF, CRLF, CR and case-insensitive MIME headers while retaining preceding text and successful bytes.
- Close an acceptance server socket if its worker cannot start; clean up smoke-test temporary directories after failures as well as successes.
- Write fixed-source snapshots as exact UTF-8 bytes and verify the stored bytes, avoiding Windows newline conversion/hash mismatch. Preserve earlier mismatched evidence rather than rewriting it.
- Let read_task wait briefly for a selected active task with cancellation and an explicit deadline, avoiding repeated model calls just to wait. Real runs observed bounded waits and truthful timeouts; request savings and autonomous recovery remain unproven.

### Added

- Extend the five-turn scripted learning HTTP checks with a lexical miss followed by an explicit overview read, retaining correction IDs, method identity, unknown mastery and post-revocation emptiness. This is not autonomous-model acceptance.

- A public-quality HTTP acceptance scenario with versioned, hashed public PDF snapshots, on-demand body reads and a separate empty human review template. No live-search fallback or automatic semantic score; reuse shared model-budget controls only with explicit live execution.

- A five-turn isolated learning acceptance scenario with per-turn persisted event/source snapshots. Scripted native-tool tests verify correction, recall and revocation; reports retain pending semantic review and do not score generated prose.
- Integration coverage for a budget-failed parent whose two submitted tasks continue: request replay stays idempotent, a new message reads actual terminal results, and partial output/receipts remain available.

- Optional DashScope-only token reservations in isolated acceptance, alongside the shared physical-request cap. Pre-reserve UTF-8 request bytes, output/reasoning allowances and overhead; refund only coherent usage from completed calls. Errors and unknown usage retain reservations, and uncertain/excess usage stops further requests. This estimates local consumption, not the cloud balance or billing ceiling.

- Shared physical inference-attempt limits for Anthropic and DashScope acceptance clients, including independent transports for foreground/background clients. This is a local limit, not a cloud free-quota guarantee.
- Frozen QF-1 synthetic sources and a four-turn `--scenario quality` HTTP acceptance path for source discovery, method/experiment comparison, publication/numeric claims and concise followups. Store source snapshots/hashes separately from human-only facts; never feed reference answers to the model or claim a semantic pass automatically.
- A staged V0 development plan and eight human review cards. Updated old development conventions to reflect the current model-led dialogue rather than the legacy fixed research loop.

### Validation

- Task handoff changes passed 464 Python tests and 14 frontend tests, including stale/current run links, excerpt limits, parent reading provenance and dual-task recovery. No new cloud-model requests; generated-summary semantics remain pending.

- Long learning-context changes passed 457 Python tests, 14 frontend tests and a 327-file preflight. Local HTTP delivered a 470-character profile reference for a 23093-character stored synthetic evidence record, preserving full UI data and source IDs. Zero cloud-model/external-network calls; semantic acceptance remains pending.

- Learning-recall changes passed 450 Python regressions, 14 frontend tests and a 325-file preflight. The isolated five-turn filter/overview lifecycle uses scripted model decisions; no real-model requests and no semantic acceptance. Prior intermittent HTTP failure remains recorded, not declared fixed.

- Frozen-public-corpus HTTP replay passed with scripted decisions and zero cloud-model/external-network calls. The initial 443-test run had one existing HTTP test abort with Windows 10053; 18 targeted Web tests, five repetitions of the failing test and the full 443-test recheck passed. Preserve the initial failure; its cause remains unestablished.

- Six local reads of the two existing public PDF caches stayed within budget; query spans matched the extracted text. No model or network requests. This checks ingestion, not autonomous research or answer correctness; see docs/v0-pdf-evidence-review-20261018.md.

- Latest full regression: 464 offline Python regressions and 14 frontend tests passed, with no WMI workaround or skipped tests. Syntax, dependency and release checks passed; test resources were cleaned up. Earlier 384/405/419/420/424/434/443/450/457-test snapshots remain in the historical review.
- Public arXiv retrieval and one query-selected PDF read succeeded. The 24 authorized real-model requests do not establish semantic acceptance; failed answers, budget stops and older hash-mismatch artifacts are retained.

### Scope

- Additional verified free-quota acceptance used separate Flash/Max ledgers: 71 requests in total. Host-assisted task continuation, persisted learning correction and a seeded three-turn recall/revocation path succeeded within their scopes. Full five-turn semantics and controlled research quality still have failures; retain them in docs/v0-quality-review-20261018.md and keep the PR draft.

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
