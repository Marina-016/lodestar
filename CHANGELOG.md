# Changelog

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
