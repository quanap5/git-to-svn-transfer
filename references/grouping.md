# Choosing groups

The script checks that a plan is complete and unambiguous. It cannot tell
whether the groups make sense. That part is yours.

## What a group is

A group is a set of paths. Each changed path belongs to exactly one group and
arrives in SVN in its final state. Consequences:

- Several Git commits that build one feature collapse into one group.
- A Git commit that mixes features can be split, **by path**. Two features that
  edit the same file cannot be separated; put that file in the group that
  cannot work without it, and say so in the rationale.
- Intermediate states of a file are not transferred. SVN gets one revision per
  group, not one per Git commit.

## Inputs to read

From `inspect`:

- `operations`: every changed path with its operation, size, binary flag.
- `by_directory`: change counts per directory, a first cut at boundaries.
- `commits` (incremental mode): subject and paths per commit, oldest first.
  Commits that touch the same paths with related subjects are one feature.
- `renames`: keep both halves of a rename in one group, or SVN history will not
  follow the file. Trust this list, not commit subjects: a commit that says
  "move" may have moved a file and then recreated the old path, which is an add
  and a modify, not a rename.

Also read the repository itself: build files, project manifests and imports
show which directories depend on which.

## Rules

1. **Group by function, not by size.** A group answers "what does this add?"
   in one sentence. Never cut a directory in half to balance file counts.
2. **Order by dependency.** A group may depend only on earlier groups. Shared
   contracts, schemas and build configuration come first; code that uses them
   next; tests after the code they test; deployment after what it deploys;
   documentation last.
3. **Keep each cumulative state coherent.** After group N, the tree should
   build and make sense with groups 1 to N only. Do not split one compilable
   unit (one project, one package, one application) across groups unless the
   parts really build alone.
4. **Keep generated files with their source.** A lock file goes with its
   manifest, a generated client with the contract it is generated from.
5. **Separate what reviews differently.** Large binary assets, vendored code
   and bulk documentation are easier to review apart from hand-written code.
6. **Respect the requested count loosely.** `desired_groups` is a target. Three
   real groups are better than ten arbitrary ones; fifteen are fine if the
   change set has fifteen functions. Correctness comes first: never keep a
   boundary that leaves a state which does not run in order to reach the count.
   State the reason when you deviate much.
7. **Deletions travel with their reason.** Put a deleted file in the group that
   replaces or obsoletes it.

## Every cumulative state must run

After group N is committed, SVN holds the baseline plus groups 1 to N, and that
is what the next person checks out. Each such state has to build, pass its
tests and keep earlier features working. This outranks every preference above:

- Draw a boundary only where the state on the near side runs without the far
  side.
- A contract and the only code that makes it meaningful, a migration and the
  code that needs it, an interface change and all its callers: these go in one
  group.
- When in doubt, merge. Fewer groups that all run beat more groups with one
  broken state. Record the reason in `rationale`.

The checks prove it; see [checks.md](checks.md) for choosing them and for what
to do when a group fails.

## Commit messages

Write one message per group in the style the SVN log already uses; read
`svn log -l 10` in the working copy if one is available. A subject line plus a
short list of what the group adds is enough. Do not mention Git commit hashes,
this tool, or an AI.

## When the plan is rejected

- `unassigned`: add the path to a group, or exclude it from the scope if SVN
  should not receive it.
- `ambiguous`: two groups claim the path with equal specificity. Make one
  pattern more specific or remove the overlap.
- `matches no changed path`: the pattern is stale; remove it.
- `depends on ... which is not an earlier group`: reorder.
