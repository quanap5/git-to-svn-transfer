---
name: git-to-svn-transfer
description: Prepare changes from a Git repository for a manual, group-by-group transfer into Subversion (SVN). Splits a Git range or a first import into functional groups, builds a temporary branch with one cumulative commit and one real Git worktree per group, runs the project's build, test and smoke checks on every cumulative worktree, exports a delta package and commit message per group, writes a step-by-step SVN guide, re-checks each group inside the SVN working copy before the user commits, verifies the result, resumes interrupted runs and cleans up. Never runs svn commit or git push. Use when the user wants to move, sync, port or mirror Git commits into SVN, commit Git work to SVN by hand in several functional commits, or continue, verify or clean up such a transfer.
license: MIT
compatibility: Requires Python 3.8+, the git CLI and an svn CLI client. Tested on WSL2 Ubuntu 22.04 with Python 3.10, Git 2.34 and the TortoiseSVN command-line client 1.14 (svn.exe driven from WSL). Other platforms are untested.
metadata:
  version: "1.2.0"
---

# Git to SVN transfer

Prepare a Git change set so a person can commit it to SVN by hand, one
functional group at a time, with every intermediate SVN revision in a state
that runs, and prove that nothing was lost or repeated.

The work is split in two. **You** decide the grouping and the checks, which are
judgements. **The script** `scripts/git_to_svn.py` does every deterministic
step and refuses a plan that does not exactly cover the change set. Run it with
`python3` from this skill's directory or by absolute path. It prints JSON on
stdout; exit code 2 means a problem the user must resolve, exit code 1 means a
check or a verification failed.

## Progress

Every command also prints its steps on stderr: the command, each numbered step,
and one line per group or check with its status.

```text
[verify] <run-dir>
 4/5 project checks on every cumulative worktree
     ✓ group 01 → build  passed  12.4s
     ✗ group 02 → test  failed  8.1s  exit 1  log <run-dir>/checks/group-02-02-test.log
 5/5 readiness
     ✓ group 01 contracts  ready
     ✗ group 02 backend  failed  required test check 'test' failed (exit 1)
[verify] ✗ exit 1 · 01 ready, 02 failed
```

- `--progress auto` (default) colours the lines on a terminal and keeps them
  plain otherwise; `plain` never colours; `off` prints none. `NO_COLOR` is
  respected.
- Parse stdout only. Do not merge stderr into it (`2>&1`) when you read the
  JSON; the progress lines are for people.
- The user does not see a command's output while you run it. After each command,
  show them where the transfer stands with a short status board built from the
  JSON, and mark the current stage:

  | Stage | Group | Status |
  |---|---|---|
  | prepare | all | done |
  | verify | 01 contracts | ready |
  | verify | 02 backend | failed: test check, see the log |
  | apply → precommit → commit → record | 01 contracts | next |

  Use the script's own status words (`ready`, `failed`, `blocked`, `unverified`,
  `verified`, `mismatch`, `not_run`) unchanged.

## Boundaries

- Never run `svn commit`, `svn import`, `git push`, or anything else that
  publishes. The user commits. The script has no code path that does.
- Never change the source repository's checked-out branch, history, index or
  uncommitted changes. Do not run `git checkout`, `reset`, `stash`, `rebase` or
  `commit` there. The script works through a private index and plumbing. What
  `prepare` does add to the repository, and `cleanup` removes, is one branch
  `svn-transfer/<run-id>` and one worktree registration per group. Tell the
  user about both.
- Never delete worktrees or run directories with `rm`. Use the `cleanup` mode,
  and only pass `--execute` when the user asked for removal.
- Do not guess the baseline. If it is not known, ask for it and nothing else.
- Correctness comes before the number of groups. Never keep a group boundary
  that leaves a state that does not run in order to reach a requested count.

## Decide the baseline first

Exactly one of these must be true, and the user must have said which:

| Situation | Option | Meaning |
|---|---|---|
| Incremental sync | `--base <git-ref>` | SVN already holds that Git state |
| First import, SVN has content | `--initial-from-svn-wc --svn-wc <dir>` | Baseline is derived from the working copy |
| First import, SVN is empty in scope | `--initial-empty` | Everything in scope is new |

Look for evidence before asking: an SVN working copy nearby, a tag or note
naming the last synced commit, `git svn` metadata. If the evidence does not
settle it, ask one question: which Git commit SVN currently matches, or that
this is a first import. Decide ordinary details (output folder, group names)
yourself.

Uncommitted changes are left out unless the user asks for them
(`--include-uncommitted`, target must be `HEAD`).

## Every group must leave a state that runs

Each SVN revision the user commits becomes a state other people check out. So
group NN's cumulative state (the baseline plus groups 01 to NN) must build, pass
its tests, and keep the features that existed before it working.

Readiness is a status the script assigns from evidence. You never assign it.

| Status | Meaning | May it be applied to SVN? |
|---|---|---|
| `ready` | Every required check that applies to the state ran and passed. | Yes. |
| `failed` | A required check ran and failed. | Never. Fix the plan. |
| `blocked` | A required check could not run here: a tool, SDK, dependency or device is missing. | Only on the user's decision (`--allow-not-ready`). |
| `unverified` | No required check applies to the state, or none has been run, or one timed out. | Only on the user's decision. |

Rules that follow:

- Find the project's real build, test and smoke commands (build files, CI
  configuration, README, package scripts) and declare them as required checks.
  See [references/checks.md](references/checks.md).
- A missing tool is `blocked`, not `ready` and not `failed`. Do not install
  toolchains, weaken a check, or swap it for a trivial one to turn a status
  green. Report it.
- A state with no required check is `unverified`. Give it a real check if one
  exists; otherwise leave it and say so.
- When a group is `failed`, the plan is wrong, not the check. Follow the loop
  in "Mode: verify".

## Source options

`inspect`, `plan` and `prepare` take the same source options, written below as
`<source options>`. Pass identical values to all three; a different base,
target or scope describes a different transfer.

| Option | Default | Meaning |
|---|---|---|
| `--repo <dir>` | current directory | Git repository. |
| one baseline option | none, required | See the table above. |
| `--target <ref>` | `HEAD` | Git state to transfer up to. |
| `--svn-wc <dir>` | none | SVN working copy. Enables the baseline and working-copy checks. Strongly preferred. |
| `--svn-bin <path>` | `G2S_SVN`, then `svn` on `PATH` | svn client. Needed when svn is not on `PATH`, for example a Windows `svn.exe` used from WSL. `prepare` stores it; later commands reuse it. |
| `--svn-url`, `--svn-revision` | none | Baseline as stated by the user when there is no working copy. Recorded as unverified. |
| `--groups <n>` | 10 | Desired group count. Echoed back by `plan` next to the actual count; never enforced. |
| `--include`, `--exclude <pattern>` | everything | Scope. Repeatable. |
| `--include-uncommitted` | off | Also take uncommitted and untracked changes. |
| `--allow-flagged <pattern>` | none | Accept paths flagged as secrets, dependencies or build output. |

## Mode: plan

1. Inspect. This is read-only.

   ```bash
   python3 scripts/git_to_svn.py inspect <source options> --json-out <file outside the repo>
   ```

   With `--json-out`, stdout is a summary and the file holds the full report:
   open the file for `operations`, `commits`, `by_directory` and the full
   `warnings`, `manual_items`, `reconcile` and `svn_only` lists.

   Read `blockers` first. A dirty working copy, mixed revisions, a baseline
   mismatch or secret-like paths stop everything; report them and let the user
   fix or exclude. Read `warnings`, `manual_items`, `reconcile` and `svn_only`
   and tell the user about each: none is handled silently.

2. Write the plan as JSON: the groups, following
   [references/grouping.md](references/grouping.md), and the checks, following
   [references/checks.md](references/checks.md). The format is in
   [references/plan-format.md](references/plan-format.md); start from
   `assets/plan.template.json`. `--groups` is a target, not a quota.

3. Validate it, and fix the plan until it passes:

   ```bash
   python3 scripts/git_to_svn.py plan <source options> --plan <plan.json>
   ```

   Look at `groups_without_a_required_check`: those states will be
   `unverified`.

4. Show the user the groups, their order, what each depends on and which checks
   gate them before preparing, unless they asked you to proceed without a
   review.

## Mode: prepare

```bash
python3 scripts/git_to_svn.py prepare <source options> --plan <plan.json> \
  --out <directory outside the repository> [--supersedes <earlier run>]
```

This creates `<out>/<run-id>/` holding `manifest.json`, `GROUPS.md`,
`SVN_MANUAL_TRANSFER.md`, `groups/NN/` (delta package and `message.txt`) and
`worktrees/group-NN` (real Git worktrees; group NN is the baseline plus groups
01 to NN). Every group starts `unverified`.

Checks normally live in the plan. `--required-check "<command>"` adds a required
check for every worktree from the command line; `--check-each` and `--check`
add optional ones that are reported but do not gate readiness.

Go straight on to verify with `--run-checks`. A run is not finished until its
checks have been run.

## Mode: verify

```bash
python3 scripts/git_to_svn.py verify --run <run-dir> --run-checks [--svn-wc <dir>] [--svn-final]
```

| Option | Effect |
|---|---|
| `--run-checks` | Run the checks on every cumulative worktree and set each group's readiness. Without it, readiness keeps its last value. |
| `--svn-wc <dir>` | Check the working copy too. Defaults to the one used at prepare; without any, the SVN checks are `not_run`. |
| `--svn-bin <path>` | Only needed to override the client stored at prepare. |
| `--svn-final` | After the last group: require the working copy to hold the final state. |
| `--check-timeout <s>` | Limit for each check. A timeout leaves the group `unverified`. |

The output has `readiness` per group and `run_ready`, which is true only when
the structural checks passed and every group is `ready`. Report each result by
its status: `passed`, `failed` or `not_run`. Do not turn `not_run`, `blocked` or
`unverified` into a pass. A green build shows a state runs; the operation and
snapshot checks are what show the transfer is complete.

### When a group fails

1. Read the log named in the result (`checks/group-NN-*.log`) and find what the
   state is missing. Usually it needs something a later group brings.
2. Change the plan, in this order of preference:
   - move the missing paths into the failing group, or move the failing paths
     later, so the boundary follows the real dependency;
   - reorder the groups if the dependency ran the wrong way;
   - merge the group with the one it depends on when they cannot run apart.
   Do not weaken or drop the check, and do not mark a failing group as an
   exception.
3. Prepare again with `--supersedes <failed run>` and run verify with
   `--run-checks` again. Repeat until no group is `failed`.
4. If a check fails on the final state too, the target itself is broken. That
   is not a grouping problem: stop and tell the user.

Superseded runs are kept and marked. List them for the user at the end with
their cleanup command; do not remove them unasked.

## Mode: resume

```bash
python3 scripts/git_to_svn.py resume --run <run-dir> [--svn-wc <dir>]
```

Use it when a run directory already exists. It reports drift (moved refs,
altered packages, changed worktrees, a working copy that moved on) and finishes
an interrupted prepare without recreating what exists. Preparing the same
transfer twice is refused and points here.

## The SVN side: apply, precommit, commit, record

The user follows `SVN_MANUAL_TRANSFER.md`. Per group, in order:

1. **Stage.** `apply` copies the package and schedules adds, deletes, moves and
   properties. It is a dry run until `--execute`, never commits, and refuses a
   `failed` group outright and any other non-`ready` group without
   `--allow-not-ready`.

   ```bash
   python3 scripts/git_to_svn.py apply --run <run-dir> --group NN --svn-wc <dir> [--execute]
   ```

   Relay `needs_manual_svn_step` and `manual` word for word; those are steps it
   could not do.

2. **Check again, in the working copy.** Passing on a Git worktree does not
   prove the SVN copy is the same: SVN can differ in files the transfer never
   touched, in line endings, in properties that rewrite content, or through a
   copying mistake. `precommit` checks the copy that will be committed:

   ```bash
   python3 scripts/git_to_svn.py precommit --run <run-dir> --group NN --svn-wc <dir>
   ```

   It confirms only this group is staged, compares every transferred file with
   the package, reports every difference between the working copy and the Git
   worktree, and runs the required checks inside the working copy. It reports
   the same four statuses. On `failed`, the user must not commit. It lists the
   unversioned files the checks created; they must not be added.

3. **The user commits.**

4. **Record.**

   ```bash
   python3 scripts/git_to_svn.py record --run <run-dir> --group NN --revision <rev> --svn-wc <dir>
   ```

   Only `verified` means the group is transferred. Without `--svn-wc` the status
   is `user_provided`; say so. On `mismatch`, show the details and stop before
   the next group.

## Mode: cleanup

```bash
python3 scripts/git_to_svn.py cleanup --run <run-dir>            # lists only
python3 scripts/git_to_svn.py cleanup --run <run-dir> --execute  # only when the user asked
```

It removes only this run's worktrees and branch, through `git worktree remove`,
and refuses a worktree with unsaved changes or untracked files; the branch stays
while any worktree remains. Checks usually leave build or test output behind,
which causes exactly this refusal. Show the user the listed files;
`--discard-untracked` removes such worktrees when the only differences are
untracked files, and never when a tracked file was edited. The manifest, guides
and packages stay.

## Reporting to the user

Give: the scope and baseline that were used; the group table with readiness,
paths and commit messages; which checks ran where and their status; every group
that is not `ready` and why; every warning, manual item and SVN/Git difference;
superseded runs; and the cleanup commands. Say plainly when a status is
`blocked` or `unverified` and what would make it `ready`. Answer in the user's
language.

## References

- [references/grouping.md](references/grouping.md): how to choose groups and their order
- [references/checks.md](references/checks.md): choosing build, test and smoke checks; readiness; fixing a failed group
- [references/plan-format.md](references/plan-format.md): plan JSON, checks, and how paths are matched
- [references/special-cases.md](references/special-cases.md): line endings, executable bit, symlinks, LFS, submodules, externals, sparse checkouts, properties, non-ASCII names
- [references/manifest.md](references/manifest.md): `manifest.json` fields and the run directory layout
- [references/install-and-environments.md](references/install-and-environments.md): installing for a project or personally, supported environments, running the tests
- [references/claude-code.md](references/claude-code.md): Claude Code specifics; other agents can ignore it
