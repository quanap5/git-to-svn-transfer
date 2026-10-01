# Plan format

A plan is a JSON file. `assets/plan.template.json` is a working example.

```json
{
  "schema_version": 1,
  "groups": [
    {
      "key": "backend",
      "title": "Backend service",
      "rationale": "Why these paths form one group.",
      "depends_on": ["contracts"],
      "paths": ["backend/", "tools/run-backend.sh"],
      "message": "feat(backend): subject line\n\n- detail\n- detail"
    }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `schema_version` | yes | Must be `1`. |
| `groups` | yes | Ordered list. The order is the transfer order; ids `01`, `02`, ... follow it. |
| `checks` | no | Build, test and smoke commands; see Checks below. Without any, every group is `unverified`. |
| `key` | yes | Lowercase letters, digits, hyphens. Unique. Used in `depends_on`. |
| `title` | yes | Short name shown in the group table. |
| `rationale` | yes | Why the paths belong together; shown in the guide. |
| `depends_on` | no | Keys of earlier groups this one needs. A later key is an error. |
| `paths` | yes | Patterns that claim changed paths. |
| `message` | yes | The SVN commit message for this group. |

## Checks

The optional top-level `checks` list names the build, test and smoke commands
that decide each group's readiness. How to choose them is in
[checks.md](checks.md).

```json
"checks": [
  {"name": "backend-build", "kind": "build", "command": "dotnet build backend/Api",
   "required": true, "requires": ["dotnet"], "from_group": "backend"},
  {"name": "backend-tests", "kind": "test", "command": "dotnet test backend/Api.Tests",
   "requires": ["dotnet"], "from_group": "backend-tests"},
  {"name": "console-types", "kind": "build", "command": "npm run typecheck",
   "cwd": "frontend/console", "requires": ["npm"], "from_group": "console"}
]
```

| Field | Required | Default | Meaning |
|---|---|---|---|
| `name` | yes | | Unique name, used in results and log file names. |
| `command` | yes | | Split into arguments and run without a shell. |
| `kind` | no | `other` | `build`, `test`, `smoke`, `lint` or `other`. |
| `required` | no | `true` | A required check gates readiness; an optional one is only reported. |
| `where` | no | `each` | `each` runs on every cumulative worktree it applies to; `final` only on the last. |
| `from_group` | no | first group | Key of the first group whose state must pass it. It applies to that group and all later ones. |
| `requires` | no | none | Executables that must be on `PATH`. A missing one gives `blocked`. |
| `cwd` | no | worktree root | Directory inside the worktree to run in. |
| `timeout_seconds` | no | `--check-timeout` | Limit for this check. |

The checks are part of what identifies a run: changing them and preparing again
creates a new run (use `--supersedes`).

## How paths are matched

Paths are relative to the repository root and use `/`.

| Pattern | Kind | Matches |
|---|---|---|
| `src/app.py` | exact | That path. Also everything under it if it is a directory. |
| `src/` | prefix | Everything under `src/`. |
| `*.md`, `src/**/test_*.py`, `**` | glob | `fnmatch` rules; `*` also crosses `/`. |

When several patterns match a path, the most specific wins: exact beats prefix,
prefix beats glob, and a longer pattern beats a shorter one of the same kind.
So `frontend/tests/` in one group takes its files from `frontend/` in another,
and a final `**` collects whatever is left.

The plan is rejected when:

- a changed path matches no group (`unassigned`),
- two groups match a path with equal specificity (`ambiguous`),
- a group or a pattern matches no changed path,
- a field is missing or `depends_on` names a later group.

Only changed paths are matched. Unchanged files need no group.

## Renames

Git reports a rename as a deleted path and an added path. If both are in the
same group, the package lists it in `rename.tsv` and SVN gets an `svn move`. If
they are in different groups, it is transferred as a delete and an add, and
`GROUPS.md` says so under Notes. The script never issues an `svn move` whose
source and destination are not in the same group.
