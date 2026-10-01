# Checks and readiness

A group is transferred as one SVN revision, and that revision is a state other
people will check out. The checks are how each cumulative state earns `ready`.

## Choosing checks

Use the project's own commands. Look in build files, CI configuration, package
scripts and the README. Prefer, in this order:

| Kind | Shows | Examples |
|---|---|---|
| `build` | The state compiles or bundles. | `dotnet build`, `npm run build`, `cargo build`, `make` |
| `test` | Existing behaviour still holds, so earlier features stay compatible. | `dotnet test`, `pytest`, `npm test`, `go test ./...` |
| `smoke` | The thing starts and answers. | a `--version` or `--help` call, a health script, importing the main module |
| `lint` | Static rules hold. Usually optional. | `eslint .`, `ruff check` |

A build alone does not show that existing features still work. Where a test
suite exists, make it required. Running the full suite on every worktree is the
point: it is what shows that group NN did not break what groups 01 to NN-1 and
the baseline provide.

## Writing a check

Checks go in the plan's `checks` list (format in
[plan-format.md](plan-format.md)).

- **`from_group`**: a check can only pass once what it tests exists. A backend
  build makes no sense before the backend group. Set `from_group` to the first
  group whose state should pass it; it then runs on that group and every later
  one. Leave it out for checks that must hold from the first group.
- **`requires`**: executables the check needs. If one is not on `PATH` the
  check is `blocked` without being run. List the toolchain here (`dotnet`,
  `npm`, `cargo`) so a missing SDK is reported as missing, not as a failure.
- **`cwd`**: a directory inside the worktree to run in.
- **No shell.** The command is split into arguments and run directly: no pipes,
  `&&`, redirection or variable expansion. Wrap those in a script that lives in
  the repository, or call `bash -c '...'` explicitly.
- **It must work in both places.** The same check runs in a Git worktree and
  later in the SVN working copy. Do not use `git` commands or anything that
  needs `.git` in a check.
- **Exit codes.** `0` passes. `77` means "a prerequisite is missing here" and
  gives `blocked`; use it in a wrapper script when a check needs a device, a
  service or a credential that may be absent. `127` (command not found) also
  gives `blocked`. Anything else is `failed`.
- **Side effects.** Checks leave build output. In worktrees that makes
  `cleanup` refuse until `--discard-untracked` is passed. In the SVN working
  copy the files show up as unversioned and must not be added; `precommit`
  lists them.

## Readiness, exactly

For one cumulative state, over the required checks that apply to it:

1. any failed: `failed`;
2. otherwise any blocked: `blocked`;
3. otherwise none apply, or one timed out or has not run: `unverified`;
4. otherwise: `ready`.

Optional checks are run and reported but change nothing above.

`run_ready` is true only when every group is `ready` and the structural checks
passed. One blocked group makes the run not ready; say which and why.

## Do not manufacture a green status

- Do not install SDKs or dependencies to unblock a check unless the user asks.
  Report `blocked` with the missing tool.
- Do not replace a real check with `true`, a trivial script, or a narrower
  command to get past a failure.
- Do not mark a check optional because it fails.
- Do not drop `from_group` earlier or later to dodge a failure; set it where the
  check truly starts to apply.

## Fixing a failed group

A failure on state NN means that state lacks something or breaks something.
Read the log first; then:

| What the log shows | Change to the plan |
|---|---|
| A file, module or symbol is missing and a later group adds it | Move those paths into this group, or move this group's dependent paths to the later one. |
| The dependency points the other way | Swap the order of the two groups. |
| Two groups only work together (a contract and its only consumer, a migration and the code that needs it) | Merge them into one group. |
| A test fails because the feature it tests arrives later | Move the test with its feature, or set the check's `from_group` to where the feature lands, if the test really belongs there. |
| It fails on the final state too | The target is broken. Stop and tell the user; no grouping fixes it. |

Then prepare again with `--supersedes <failed run>` and run the checks again.
Fewer, larger groups that all run are better than more groups with one that does
not. Explain each merge in the group's `rationale`.

## Why the working copy is checked again

`ready` was earned on a Git worktree. The SVN working copy is a different
directory with a different history, and it can differ from the worktree:

- in files the transfer does not touch, which the baseline check never compares;
- in line endings, when `svn:eol-style` or a Windows checkout rewrites them;
- through `svn:keywords` expansion;
- in files that exist only in SVN;
- through a manual copy that missed or mangled a file.

`precommit` therefore compares the transferred files with the package, reports
every other difference between the working copy and the worktree's tree, and
runs the required checks inside the working copy. A group whose worktree was
`ready` can be `failed` there, and then it must not be committed until the cause
is understood. `precommit --skip-checks` compares content only and can at best
report `unverified`.
