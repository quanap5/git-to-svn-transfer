
### Group $id: $title

- **Why this is one group:** $rationale
- **Depends on:** $depends
- **Transfer commit (on the temporary branch only):** `$commit`
- **Worktree (cumulative state):** `$worktree`
- **Package (this group only):** `$package`
- **Readiness on the cumulative worktree:** $readiness
- **Operations:** $adds added, $modifies modified, $deletes deleted, $renames renamed; $binaries binary

Files:

$files

Commit message, also in `message.txt`:

```text
$message
```

Checks that apply to this state:

$group_checks

Why it is not ready, if it is not:

$readiness_reasons

Property changes:

$properties

Manual items:

$manual

Follow steps 1 to 7 above with `N=$id`.
