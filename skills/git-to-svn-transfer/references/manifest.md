# Run directory and manifest

## Layout

```text
<out>/<run-id>/
  manifest.json              source of truth for the run
  plan.json                  the plan as it was given
  GROUPS.md                  group table, warnings, differences
  SVN_MANUAL_TRANSFER.md     the guide the user follows
  dashboard.html             readable view of the run, rebuilt after every command; open it in a browser
  events.jsonl               one line per command run on this run: at, command, exit_code, summary, group
  groups/NN/
    payload/                 added and modified files of group NN only
    add.txt modify.txt delete.txt     one path per line
    rename.tsv               old<TAB>new
    svn-targets-add.txt svn-targets-delete.txt   paths escaped for svn --targets
    executable.txt           set|unset<TAB>path
    manual.txt               op<TAB>path<TAB>reason for items not in the package
    sha256sums.txt           for sha256sum -c inside the working copy
    message.txt              SVN commit message
    operations.json          the group's operations in full
  worktrees/group-NN/        Git worktree: baseline + groups 01..NN
  checks/                    logs: group-NN-*.log from the worktrees, svn-group-NN-*.log from the working copy
```

The run id is `<date>-<time>-<first 8 of the target SHA>`. The temporary branch
is `svn-transfer/<run-id>` and points at the last group's commit. Worktrees are
detached at their group's commit. Reports and packages sit outside the
worktrees, so they never appear as source files.

## manifest.json

| Field | Meaning |
|---|---|
| `schema_version` | `1`. Other versions are refused. |
| `tool_version`, `run_id`, `created_at`, `prepared_at` | Identity of the run. |
| `state` | `planned` until prepare finishes, then `prepared`. |
| `mode` | `incremental`, `initial-svn-wc` or `initial-empty`. |
| `fingerprint` | Hash of mode, base tree, target tree, scope and plan. A second prepare with the same fingerprint is refused. |
| `repository` | Absolute path of the source repository. |
| `base` | `ref`, `sha` (null in first-import modes), `tree`. |
| `target` | `ref`, `sha`, `tree`, `include_uncommitted`. |
| `scoped_target_tree` | Tree the last group must equal: the base with every in-scope operation applied. |
| `scope` | `include` and `exclude` patterns. |
| `svn_baseline` | `source`, `working_copy`, `url`, `repository_uuid`, `revision`, `checked_at`. `source` says whether it was read from a working copy or only stated by the user. |
| `branch`, `baseline_commit` | The temporary branch and the commit the chain starts from. |
| `groups[]` | See below. |
| `manual_items`, `reconcile`, `svn_only`, `out_of_scope` | What is not transferred, with reasons. |
| `warnings`, `notes` | Special cases found at prepare time. |
| `check_commands` | The checks: `name`, `kind`, `command`, `required`, `where`, `from_group`, `requires`, `cwd`, `timeout_seconds`. |
| `check_results` | One entry per check per worktree: `status` (`passed`, `failed`, `blocked`, `timed_out`), `exit_code`, `reason`, `log`. |
| `verification` | Result of the last `verify`: `summary`, `readiness` per group, `run_ready`, `results`. |
| `supersedes`, `superseded_by` | Links between a run and the one that replaced it after a failed group. A superseded run cannot be applied. |
| `resume_log` | What each resume had to redo. |
| `cleanup` | What cleanup removed and what it refused. |

Each group:

| Field | Meaning |
|---|---|
| `id`, `key`, `title`, `rationale`, `depends_on`, `patterns`, `message` | From the plan. `id` is the position, `01` upward. |
| `commit`, `tree` | The cumulative commit and its tree. |
| `worktree`, `package` | Paths relative to the run directory. |
| `packaged` | True once the package was written. |
| `operations[]` | One entry per changed path. |
| `readiness` | `status` (`ready`, `failed`, `blocked`, `unverified`), `reasons`, `at`. Set by `verify --run-checks` from the checks on this group's cumulative worktree. |
| `svn_precommit` | Result of the last `precommit` in the working copy: `status`, `reasons`, `staged`, `identical_to_git_worktree`, `differences_outside_transfer`, `svn_only_files`, `checks`, `unversioned_files_created_by_checks`. Null until run. |
| `svn_revision` | `revision`, `status`, `recorded_at`, `details`. |

Each operation:

| Field | Meaning |
|---|---|
| `path`, `op` | `add`, `modify` or `delete`. |
| `kind` | `file`, `symlink` or `submodule`. |
| `old_mode`, `new_mode`, `old_blob`, `new_blob` | Git modes and object ids. |
| `sha256`, `size`, `binary`, `eol` | Facts about the new content. `eol` is `lf`, `crlf`, `mixed`, `none` or `binary`. |
| `before_sha256` | Checksum the working copy must hold before a modify or delete. |
| `sha256_lf`, `before_sha256_lf` | The same with line endings normalised. |
| `exec` | `set`, `unset` or null. |
| `rename_from`, `rename_to` | Set on both halves of a rename kept in one group. |
| `manual` | Reason the item is not packaged, or null. |
| `flag` | Why the path looks like a secret, dependency or build output, or null. |

## Revision status

| Status | Meaning |
|---|---|
| null | Nothing recorded. |
| `user_provided` | The number was stated; nothing was compared. Not transferred. |
| `mismatch` | The revision or the working copy does not match the group. `details` lists why. Not transferred. |
| `verified` | The revision changes exactly the group's paths with the expected actions, and the working copy content matches the package. |

`verified` is about the revision's content. Whether the working copy was shown
to run before the commit is `svn_precommit.status`; when it was not `ready`,
`record` says so in `details`.

A group counts as transferred only when `verified`. `apply` refuses a group
while an earlier one is not, unless `--allow-unverified-previous` is passed.
