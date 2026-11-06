# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-06

### Added

- `cgrid` opens 1–12 panes (6 by default) as a tmux grid in one window: a new window in
  Terminal.app or iTerm2, or the current window elsewhere.
- A picker in each pane ranks folders from Claude Code's `history.jsonl` and session transcripts,
  weighted by recency and number of prompts. Worktrees count toward their repo.
- The permission mode is learned per folder from transcripts (skip-perms, auto, accept-edits, plan
  or ask) and can be changed with ← → before launch.
- Panes default to different projects and skip folders other panes already took.
- Fuzzy search, path browsing with Tab, and folders under common roots that were never opened with
  Claude.
- Presets by name or path, plus `--top`, `--last`, `--continue` and `--mode`.
- Optional `--ai` ranking through `claude -p` (Haiku), running in the background.
- `pick`, `top`, `ls`, `attach`, `kill`, `config` and `doctor` commands. `ls` shows memory, CPU and
  process count for every pane.
- Idle pickers redraw only when something changes, at about 0.006% CPU each.

[Unreleased]: https://github.com/nikakirkitadze/cgrid/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/nikakirkitadze/cgrid/releases/tag/v0.1.0
