# Installing, environments, tests

## Install

Nothing is built and nothing is installed with pip.

### As a Claude Code plugin

The repository is a plugin and its own marketplace. In Claude Code:

```text
/plugin marketplace add quanap5/git-to-svn-transfer
/plugin install git-to-svn-transfer@git-to-svn-transfer
```

The install scope (user, project or local) is chosen at install time. The skill
is then invoked as `/git-to-svn-transfer:git-to-svn-transfer`. To try a local
clone without installing it, start Claude Code with
`claude --plugin-dir /path/to/clone`.

### As a plain skill directory

The skill is the self-contained directory `skills/git-to-svn-transfer/` of the
repository. Copy or symlink that directory, keeping its name:

| Scope | Location | Use when |
|---|---|---|
| Personal | `~/.claude/skills/git-to-svn-transfer/` | You transfer several repositories from this machine. |
| Project | `<repo>/.claude/skills/git-to-svn-transfer/` | The team should get the skill with the repository. Commit it. |

Agents that follow the Agent Skills layout but use another directory (for
example `.agents/skills/`) take the same folder unchanged. Installed this way
the skill is invoked as `/git-to-svn-transfer`.

### Without an agent

```bash
python3 /path/to/skills/git-to-svn-transfer/scripts/git_to_svn.py --help
```

## Requirements

- Python 3.8 or newer. Standard library only.
- `git` on `PATH`, with worktree support.
- An `svn` command-line client. It is found through `--svn-bin`, then the
  `G2S_SVN` environment variable, then `svn` or `svn.exe` on `PATH`. TortoiseSVN
  installs one only if "command line client tools" was selected.

## Tested environment

| Component | Version |
|---|---|
| OS | WSL2, Ubuntu 22.04, repositories on a Windows drive (`/mnt/d`) |
| Python | 3.10.12 |
| Git | 2.34.1 |
| SVN client | TortoiseSVN command-line client 1.14.5 (`svn.exe`, driven from WSL) |
| SVN server | Local repository created with `svnadmin`, reached through `file://` |

Not tested: a native Linux or macOS `svn` client, native Windows Python, SVN
over `svn://` or `https://`, repositories with tens of thousands of changed
files. The code has paths for a POSIX client and for native Windows; treat them
as unproven until the test suite has run there.

Driving a Windows `svn.exe` from WSL has two consequences the scripts handle:
paths are converted with `wslpath`, and the working copy must be on a drive
Windows can open (not inside the Linux filesystem).

## Running the tests

The suite creates throwaway Git repositories and a local SVN repository with
`svnadmin`. It never contacts a real server. The tests live in `tests/` at the
root of the repository, outside the skill directory, so they need a clone.

```bash
cd /path/to/clone
python3 -m unittest discover -s tests
```

| Variable | Purpose |
|---|---|
| `G2S_SVN` | svn executable, if not on `PATH`. |
| `G2S_SVNADMIN` | svnadmin executable, if not on `PATH`. |
| `G2S_TEST_TMP` | Directory for temporary data. Required with a Windows client under WSL; it must be on a Windows drive. |

With a Windows client under WSL:

```bash
export G2S_SVN="/mnt/c/Program Files/TortoiseSVN/bin/svn.exe"
export G2S_SVNADMIN="/mnt/c/Program Files/TortoiseSVN/bin/svnadmin.exe"
export G2S_TEST_TMP=/mnt/d/tmp/g2s-tests
python3 -m unittest discover -s tests
```

The tests are skipped, with the reason, when no svn client or svnadmin is found.

## What the tests cover

- A three-group incremental transfer, applied and committed to a local SVN
  repository, ending with the working copy equal to the Git target.
- Add, modify, delete and rename; binary files; names with spaces, `@` and
  Unicode; the executable bit.
- Baseline mismatch, a dirty working copy, and an SVN change made after prepare.
- First import with identical, differing and SVN-only files.
- Resume after an interruption, a repeated prepare, an altered package, a moved
  target ref and a changed worktree.
- Revision recording: stated, verified, mismatching, and replacing.
- Cleanup: listing by default, refusing unsaved changes, leaving other runs and
  the user's own worktrees and branches alone.
- Plan validation, renames split across groups, symlinks, submodules, LFS
  pointers, CRLF, secret-like paths, scope filters and uncommitted changes.
- A file replaced by a directory of the same name, and a plan order that would
  make one path both a file and a directory.
- A non-ASCII name the Windows client cannot be given: the add is done by its
  real name, the delete is reported and the partial commit is a `mismatch`.
- Readiness: a group whose cumulative state fails a required check is `failed`
  and cannot be applied; regrouping with `--supersedes` makes every group
  `ready`; a missing tool, a missing command and exit code 77 give `blocked`; a
  state with no required check is `unverified`.
- The pre-commit check in the working copy: a group `ready` on its Git worktree
  is `failed` in a working copy that differs in an untouched file; a tampered
  transferred file, a stray staged path and "nothing staged" are caught; check
  output is listed; skipping the checks gives `unverified`.
- The command line end to end, and the rendered guide having no unfilled field.
- That no script contains a committing or pushing command.
- Progress reporting: stderr only, plain text when stderr is not a terminal,
  colour and `NO_COLOR` on a terminal, ASCII marks on a console that cannot
  encode the glyphs (simulated), `--progress off`, and stdout staying valid JSON.

Beyond the suite, the generated guide's by-hand commands were followed verbatim
once against a local SVN repository, and a fresh agent completed plan, prepare,
verify and a dry-run apply from `SKILL.md` alone.
