# Transfer groups for run $run_id

$superseded_note$readiness_note

| Item | Value |
|---|---|
| Mode | $mode |
| Git repository | `$repository` |
| Base | `$base` |
| Target | `$target` |
| SVN baseline | $svn_baseline |
| Scope include | $include |
| Scope exclude | $exclude |
| Temporary branch | `$branch` |
| Run directory | `$run_dir` |

## Order of transfer

Transfer the groups top to bottom. A group may depend only on groups above it.

| Group | Function | Transfer commit | Add | Modify | Delete | Rename | Manual | Depends on | Readiness | Pre-commit in SVN | SVN revision |
|---|---|---|---|---|---|---|---|---|---|---|---|
$rows

The transfer commits exist only on the temporary branch; they are not commits
of your own history. Each worktree `worktrees/group-NN` holds the baseline plus groups 01 to NN. Each
package `groups/NN` holds only what group NN changes. Step-by-step instructions
are in `SVN_MANUAL_TRANSFER.md`.

Readiness is `ready` only when every required check passed on that group's
cumulative worktree. `failed` means a required check failed; `blocked` means it
could not run because a tool or dependency is missing here; `unverified` means
no required check applies or none has been run. "Pre-commit in SVN" is the same
judgement made again inside the SVN working copy, which is the copy that gets
committed.

## Conditions that need attention

$warnings

## Notes

$notes

## Differences between SVN and Git that were not transferred

These paths exist on both sides with different content. Nothing was taken from
Git for them; decide per path which side is right.

$reconcile

## Paths that exist only in SVN

Left untouched.

$svn_only

## Project checks

$checks
