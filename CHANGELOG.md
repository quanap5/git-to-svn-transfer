# Changelog

All notable changes to this project are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version is the one in `.claude-plugin/plugin.json`, which is also what
`git_to_svn.py --version` prints and what `SKILL.md` carries in its metadata.

## [Unreleased]

### Added

- This changelog.

### Changed

- The README's dashboard section is now a usage guide: how to open the page,
  what each part shows, how to rebuild it, and its limits.

## [1.3.0] - 2026-10-02

### Added

- `dashboard.html` in every run directory: one self-contained page with the
  groups and their add, modify, delete and rename counts, the file tree of each
  group (changed files only, or the whole cumulative worktree), check results
  with links to their logs, the last verification and a timeline. It is rebuilt
  after every command that works on a run.
- `events.jsonl` in every run directory: one line per command with its time,
  exit code, summary and group. The dashboard's timeline is read from it.
- `report --run <run-dir>` rebuilds the dashboard without doing anything else.
  It also works on a run created by an earlier version.
- The JSON result of `prepare` has a new `dashboard` key with the page's path.

### Changed

- `cleanup` lists `dashboard.html` and `events.jsonl` among the files it keeps.

## [1.2.0] - 2026-10-02

### Added

- Step-by-step progress on stderr for every command: the command, each numbered
  step, and one line per group or check with its status and duration.
- `--progress auto|plain|off` on every subcommand. `auto`, the default, colours
  the lines on a terminal and keeps them plain otherwise. `NO_COLOR` is
  respected, and a console that cannot encode the status glyphs gets ASCII marks.
- `SKILL.md` asks the agent to show a stage, group and status board after each
  command, and to parse stdout only.

### Changed

- stdout is unchanged and still carries only the JSON result. A caller that
  merges stderr into stdout (`2>&1`) before parsing must stop doing so, or pass
  `--progress off`.

## [1.1.0] - 2026-10-02

First public release, as a Claude Code plugin that is also its own marketplace.

### Added

- The `git-to-svn-transfer` skill: `inspect`, `plan`, `prepare`, `verify`,
  `resume`, `apply`, `precommit`, `record` and `cleanup`. It splits a Git range
  or a first import into functional groups, builds one cumulative commit and one
  Git worktree per group on a temporary branch, runs the project's checks on
  every cumulative state, exports a delta package and a commit message per
  group, and writes a guide for the SVN side. It never runs `svn commit` or
  `git push`.
- Plugin and marketplace manifests in `.claude-plugin/`, so the skill installs
  with `/plugin marketplace add quanap5/git-to-svn-transfer` and
  `/plugin install git-to-svn-transfer@git-to-svn-transfer`.
- The MIT license.
- An animated banner and an install and usage guide in the README.

### Changed

- The skill moved from the repository root into `skills/git-to-svn-transfer/`.
  Installed as a plugin, it is invoked as
  `/git-to-svn-transfer:git-to-svn-transfer`.

[Unreleased]: https://github.com/quanap5/git-to-svn-transfer/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/quanap5/git-to-svn-transfer/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/quanap5/git-to-svn-transfer/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/quanap5/git-to-svn-transfer/releases/tag/v1.1.0
