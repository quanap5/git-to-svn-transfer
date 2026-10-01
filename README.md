# git-to-svn-transfer

An agent skill that prepares changes from a Git repository for a manual,
group-by-group transfer into Subversion. It follows the
[Agent Skills](https://agentskills.io/specification) layout and works with
Claude Code; the scripts also run on their own.

It never runs `svn commit` or `git push`. You commit to SVN yourself.

## What it does

- Splits a Git range, or a first import, into functional groups from a plan.
- Builds a temporary branch with one cumulative commit and one real Git worktree
  per group, without touching the source repository's branch, index or
  uncommitted changes.
- Runs the project's build, test and smoke checks on every cumulative worktree
  and marks each group `ready`, `failed`, `blocked` or `unverified`.
- Exports a delta package and a commit message per group, and writes a
  step-by-step guide for the SVN side.
- Re-checks each staged group inside the SVN working copy before you commit.
- Verifies that no change is missing or repeated, resumes interrupted runs, and
  cleans up only what it created.

## Install

Clone the repository as a skill directory. The directory name must stay
`git-to-svn-transfer`.

```bash
# personal: available in every project on this machine
git clone <this-repo-url> ~/.claude/skills/git-to-svn-transfer

# project: shared with the repository
git clone <this-repo-url> <repo>/.claude/skills/git-to-svn-transfer
```

Requirements: Python 3.8+, `git`, and an `svn` command-line client. Standard
library only; nothing to `pip install`.

## Use

With an agent: ask it to move Git changes into SVN, or run
`/git-to-svn-transfer`. The agent reads [SKILL.md](SKILL.md).

Without an agent:

```bash
python3 scripts/git_to_svn.py --help
python3 scripts/git_to_svn.py inspect --repo <repo> --base <git-ref> --svn-wc <svn-wc>
```

## Documentation

- [SKILL.md](SKILL.md): modes, boundaries and the workflow
- [references/grouping.md](references/grouping.md): choosing groups
- [references/checks.md](references/checks.md): checks and readiness
- [references/plan-format.md](references/plan-format.md): the plan file
- [references/special-cases.md](references/special-cases.md): line endings, symlinks, LFS, submodules, non-ASCII names
- [references/manifest.md](references/manifest.md): run directory and manifest
- [references/install-and-environments.md](references/install-and-environments.md): tested environment and running the tests

## Tested environment

WSL2 Ubuntu 22.04, Python 3.10, Git 2.34, and the TortoiseSVN command-line
client 1.14 driven from WSL. Other platforms are untested.

## Tests

The suite uses throwaway Git repositories and a local SVN repository created
with `svnadmin`; it never contacts a real server.

```bash
python3 -m unittest discover -s tests
```

See [references/install-and-environments.md](references/install-and-environments.md)
for the environment variables a Windows svn client under WSL needs.
