# Changelog

## [Unreleased] - 2026-08-24

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
