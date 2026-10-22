---
name: what-next
description: Guide the user, one step at a time, through committing a prepared git-to-svn-transfer run into SVN by hand. Reads the run's state, names the next group and the next step, gives the exact copy, svn add, delete, move and commit commands for that group, checks the working copy before and after the user commits, and records the revision. Never copies files into the working copy and never runs svn commit. Use after git-to-svn-transfer has prepared a run, when the user asks what to do next, how to copy a group into SVN, how to commit it, or where a transfer stands.
license: MIT
compatibility: Needs the git-to-svn-transfer skill installed next to this one (it runs that skill's scripts/git_to_svn.py), Python 3.8+, git and an svn command-line client.
metadata:
  version: "1.6.0"
---

# What next

A run prepared by `git-to-svn-transfer` holds everything needed to commit each
group to SVN by hand. This skill walks the user through it: one group, one step
at a time, with the real paths and file lists of that group.

The tool decides where the run stands. You explain it, run the read-only
checks, and wait for the user at every step that changes the working copy.

## The command

The script lives in the sibling skill: `../git-to-svn-transfer/scripts/git_to_svn.py`
relative to this skill's directory. Call it by absolute path.

```bash
python3 <git-to-svn-transfer>/scripts/git_to_svn.py next --run <run-dir> [--svn-wc <dir>] [--svn-bin <path>]
```

It reads `manifest.json` only: it runs no git or svn command and changes
nothing. Parse stdout as JSON; exit code 2 means the path is not a run.

| Field | Use |
|---|---|
| `stage` | Where the run stands. See the table below. |
| `why` | One sentence to tell the user. |
| `decision` | A choice only the user can make, or null. Ask it; never make it. |
| `progress` | Group count, verified groups, the next group. |
| `group`, `files` | The next group: readiness, precommit and revision status, message, and its add, modify, delete, rename, executable and manual lists. |
| `missing` | Something the commands need that the run does not know, such as the working copy. |
| `setup` | Shell lines that set `SVN`, `RUN`, `WC`, `TOOL`, `N` and `native`. |
| `steps` | Ordered steps, each with `title`, `by` (`you` or `agent`), `commands` and `expect`. |

## Find the run

If the user does not name a run directory, look for the newest directory
holding a `manifest.json` under the `--out` folder used earlier in the
conversation, or ask for it. Prefer a run that is not superseded.

## The loop

1. Run `next`. If `missing` is not empty, ask for what is missing (normally the
   SVN working copy) and run `next` again with it.
2. Show where things stand, in the user's language: a short line per group
   (`verified`, next, waiting), the `stage` and `why`. Give the dashboard path
   once.
3. If `decision` is set, put it to the user and stop until they answer.
4. Give the `setup` lines once per shell session, ready to paste.
5. Go through `steps` in order, one at a time:
   - `by: agent`: run the commands yourself, show the result, and compare it
     with `expect`. If it does not match, stop and explain; do not move on.
   - `by: you`: give the commands with a sentence on what they do, then wait
     until the user says they ran them. Never run these yourself. They copy
     into the working copy, schedule changes, update, commit or remove.
6. After the user commits, ask for the revision number (`Committed revision
   <REV>.`), or read it with `svn log -l 1` in the working copy, then run the
   `record` step with it.
7. Run `next` again. Repeat until `stage` is `done`.

Explain each step the first time it appears in a session: why copy only from
`payload/`, why renames go through `svn move`, what `?`, `!`, `C` mean in `svn
status`. Keep it short after that.

## Stages

| Stage | What it means | What you do |
|---|---|---|
| `resume` | The run was not fully prepared. | Run the step, then `next` again. |
| `superseded` | A newer run replaced this one. | Switch to it. Nothing from this run goes to SVN. |
| `verify` | The checks never ran, so no group is `ready`. | Run the step; it can take minutes. |
| `fix_plan` | A group failed its checks: the plan is wrong. | Stop. Hand over to the `git-to-svn-transfer` skill ("When a group fails"). |
| `stage` | The next group goes into the working copy. | Walk the steps; the last one is `precommit`. |
| `fix_precommit` | The check inside the working copy failed. | Show the reasons, have the user undo and stage again. Never commit. |
| `commit` | The group is staged and checked. | The user commits; you record. |
| `verify_record` | A revision was stated but never compared. | Run the step with the working copy. |
| `resolve_mismatch` | The recorded revision does not match the group. | Show the details, help the user correct SVN, then record again. |
| `final_verify` | Every group is verified. | Run the final check. |
| `cleanup` | The transfer is done; worktrees and branch remain. | List them; remove only if the user asks. |
| `done` | Nothing is left. | Say so, with the revision of each group. |

## When the manifest and the working copy disagree

The manifest cannot see the working copy. Trust `svn status` over it:

- At `stage`, if the clean-check step shows changes, ask whether the user
  already copied this group. If the changes are exactly this group's files,
  skip to "Look at what is staged". Otherwise stop: something else is in the
  working copy.
- At `commit`, if `svn status` prints nothing, the user may already have
  committed. Find the revision with `svn log -l 3` and record it.

## Boundaries

- Never copy files into the working copy, never run `svn add`, `delete`,
  `move`, `revert`, `update` or `commit`, and never run `apply --execute` or
  `cleanup --execute`. Those are `you` steps.
- Never skip `precommit` or `record`. Only `verified` means a group is in SVN.
- A `failed` group never goes to SVN. A `blocked` or `unverified` one goes only
  on the user's explicit decision; note it in your summary.
- One group at a time, in order. Do not start a group before the previous one
  is `verified`.
- Answer in the user's language. Keep the tool's status words unchanged.

The full by-hand guide of the run is `SVN_MANUAL_TRANSFER.md`; it also has a
TortoiseSVN route for users who prefer the GUI.
