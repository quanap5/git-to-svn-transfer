# git-to-svn-transfer

![git-to-svn-transfer: Git commits are split into functional groups, each cumulative state is built and tested, and you commit each group to SVN yourself](assets/readme-banner.gif)

A Claude Code plugin that prepares changes from a Git repository for a manual,
group-by-group transfer into Subversion. This repository is also its own plugin
marketplace. The skill inside follows the
[Agent Skills](https://agentskills.io/specification) layout, and the scripts
also run on their own.

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

In Claude Code, add this repository as a marketplace and install the plugin:

```text
/plugin marketplace add quanap5/git-to-svn-transfer
/plugin install git-to-svn-transfer@git-to-svn-transfer
```

The same from a shell:

```bash
claude plugin marketplace add quanap5/git-to-svn-transfer
claude plugin install git-to-svn-transfer@git-to-svn-transfer
```

Update later with `/plugin marketplace update git-to-svn-transfer`. A new
version is picked up when `version` in
[.claude-plugin/plugin.json](.claude-plugin/plugin.json) changes.

Other ways to install are in
[install-and-environments.md](skills/git-to-svn-transfer/references/install-and-environments.md).

Requirements: Python 3.8+, `git`, and an `svn` command-line client. Standard
library only; nothing to `pip install`. TortoiseSVN installs the `svn` client
only if "command line client tools" was selected in its installer.

Restart Claude Code, or run `/reload-plugins`, so the skill shows up.

## Use

Open Claude Code in the Git repository you want to transfer. Ask in your own
words, or call the skill by name:

```text
/git-to-svn-transfer:git-to-svn-transfer transfer the commits from <base> to HEAD into the SVN working copy at D:\svn\myproject, in about 5 groups
```

Tell the agent three things:

- **The baseline.** Which Git commit SVN currently matches (`--base <ref>`), or
  that this is a first import. The skill never guesses it and asks when it is
  missing.
- **The SVN working copy.** The directory you checked out with `svn checkout`.
- **The svn client**, when `svn` is not on `PATH`. For a Windows client driven
  from WSL, for example `/mnt/c/Program Files/TortoiseSVN/bin/svn.exe`.

What happens next:

1. **Plan.** The agent inspects the repository, splits the changes into
   functional groups, picks the project's build and test commands as checks,
   and shows you the plan.
2. **Prepare.** A run directory is created outside the repository, with one Git
   worktree, one delta package and one `message.txt` per group, plus the guide
   `SVN_MANUAL_TRANSFER.md`.
3. **Verify.** The checks run on every cumulative state. Each group becomes
   `ready`, `failed`, `blocked` or `unverified`; only `ready` should go to SVN.
4. **Transfer, one group at a time, in order.**
   - `apply` copies the files into the working copy and schedules adds,
     deletes and moves.
   - `precommit` checks the group again inside the working copy.
   - **You run `svn commit`** with the text in `message.txt`.
   - `record` stores the revision you committed.
5. **Clean up.** Ask the agent to remove the worktrees and the temporary branch
   `svn-transfer/<run-id>`.

If a run is interrupted, ask the agent to resume the run directory; it
continues instead of starting over.

Good to know:

- The source repository's branch, index and uncommitted changes are never
  touched. Uncommitted changes are left out unless you ask for them.
- With a Windows `svn.exe` driven from WSL, the working copy and the run
  directory must be on a Windows drive (`/mnt/c`, `/mnt/d`), not in the Linux
  filesystem.

The agent's full instructions are in
[SKILL.md](skills/git-to-svn-transfer/SKILL.md).

### Without an agent

From a clone of this repository:

```bash
python3 skills/git-to-svn-transfer/scripts/git_to_svn.py --help
python3 skills/git-to-svn-transfer/scripts/git_to_svn.py inspect --repo <repo> --base <git-ref> --svn-wc <svn-wc>
```

The subcommands are `inspect`, `plan`, `prepare`, `verify`, `resume`, `apply`,
`precommit`, `record` and `cleanup`. You write the plan file yourself; its
format is in
[plan-format.md](skills/git-to-svn-transfer/references/plan-format.md).

## Layout

```text
.claude-plugin/
  marketplace.json          marketplace catalog; lists this repository as one plugin
  plugin.json               plugin manifest and version
skills/git-to-svn-transfer/
  SKILL.md                  what the agent reads
  scripts/                  the transfer tool, plain Python
  references/               detail the agent loads when needed
  assets/                   plan and guide templates
tests/                      test suite; not part of the skill
assets/                     README banner and its motion-graphic source
```

## Documentation

- [SKILL.md](skills/git-to-svn-transfer/SKILL.md): modes, boundaries and the workflow
- [grouping.md](skills/git-to-svn-transfer/references/grouping.md): choosing groups
- [checks.md](skills/git-to-svn-transfer/references/checks.md): checks and readiness
- [plan-format.md](skills/git-to-svn-transfer/references/plan-format.md): the plan file
- [special-cases.md](skills/git-to-svn-transfer/references/special-cases.md): line endings, symlinks, LFS, submodules, non-ASCII names
- [manifest.md](skills/git-to-svn-transfer/references/manifest.md): run directory and manifest
- [install-and-environments.md](skills/git-to-svn-transfer/references/install-and-environments.md): tested environment and running the tests

## Tested environment

WSL2 Ubuntu 22.04, Python 3.10, Git 2.34, and the TortoiseSVN command-line
client 1.14 driven from WSL. Other platforms are untested.

## Tests

The suite uses throwaway Git repositories and a local SVN repository created
with `svnadmin`; it never contacts a real server.

```bash
python3 -m unittest discover -s tests
```

See [install-and-environments.md](skills/git-to-svn-transfer/references/install-and-environments.md)
for the environment variables a Windows svn client under WSL needs.

## License

[MIT](LICENSE)
