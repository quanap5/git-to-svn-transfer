# Claude Code specifics

Everything in `scripts/` is plain Python driving the Git and SVN command lines.
Nothing there knows about Claude Code. This page collects what is specific to
running the skill inside it; other agents can skip it.

## Invocation

- By name: `/git-to-svn-transfer <what to transfer>`.
- By description: asking to move, sync or port Git changes into SVN.

Claude Code announces the skill's base directory when the skill loads. Call the
script by that absolute path:

```bash
python3 "<skill base directory>/scripts/git_to_svn.py" inspect --repo . --base <ref>
```

## Where to put things

- `--out` must be outside the source repository. A sibling directory of the
  repository works; so does the session scratchpad for short-lived runs, unless
  the svn client is a Windows executable, which needs a Windows drive.
- Write `plan.json` and the `inspect` report in the same place, not in the
  repository.

## Asking the user

Use the question tool only for what the repository cannot answer: the baseline,
whether uncommitted changes are included, and whether a directory of internal
material belongs in SVN. Find evidence first (a neighbouring working copy,
`svn info`, `svn log`), and put what you found in the question.

## Permissions and hooks

- The script runs `git` and `svn` as subprocesses without a shell. Expect a
  permission prompt for the first `python3 .../git_to_svn.py` call.
- `cleanup --execute` and `apply --execute` change state outside the session's
  files. Run them only on an explicit request.
- A project hook that blocks a command is a decision someone made. Report it and
  let the user adjust the hook or run the command themselves.

## Long runs

`prepare` on a large change set can take minutes on a Windows drive, mostly in
worktree checkout. Run it in the foreground with a generous timeout. If it is
interrupted, `resume --run <dir>` finishes it.

## Working alongside other sessions

The script never switches the source repository's branch or touches its index,
so it is safe while another session works in the same checkout. It does add
entries to `git worktree list` and one branch under `svn-transfer/`; mention
both when reporting.
