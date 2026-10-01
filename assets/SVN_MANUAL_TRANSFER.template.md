# Manual SVN transfer for run $run_id

$superseded_note$readiness_note

This run prepared $group_count group(s) from Git for you to commit to SVN one at
a time. Nothing has been committed to SVN and nothing has been pushed to Git.
Every `svn commit` below is yours to run.

| Item | Value |
|---|---|
| Mode | $mode |
| Git base | `$base` |
| Git target | `$target` |
| SVN baseline | $svn_baseline |
| Scope include | $include |
| Scope exclude | $exclude |

## Rules that keep the transfer correct

- Use a dedicated SVN working copy, separate from the Git repository and from
  this run directory. Do not develop in it during the transfer.
- Copy only from a group's `payload/` folder. It holds exactly that group's added
  and modified files, so nothing outside the group can be carried along.
- Never copy a worktree folder wholesale: it contains a `.git` file and every
  earlier group.
- Never use a mirroring copy (`rsync --delete`, `robocopy /MIR`). Files that
  exist only in SVN must stay.
- Never touch `.svn`.
- Transfer groups in order, and record each revision before starting the next.

## Setup, once

```bash
SVN="$svn_bin"
RUN="$run_dir"
WC="$svn_wc"
TOOL="$tool"
REPO="$repository"
$native_fn
```

`native` turns a path into the form the svn client understands. It is used
wherever a file is handed to svn.

The commands below are for a POSIX shell (Linux, macOS, WSL, Git Bash). A
TortoiseSVN route is given at the end of the steps.

## Steps for every group

Set the group number, then follow the seven steps.

```bash
N=01
cd "$$WC"
```

### 1. Update and confirm the working copy is clean and at the baseline

```bash
"$$SVN" update
"$$SVN" status                       # must print nothing
"$$SVN" info --show-item revision    # $baseline_revision before group 01; the previous group's revision after that
```

Stop if `svn status` prints anything, or if the revision is not the one you
expect: someone else committed, and the group must be checked against that change.

### 2. Copy the group's added and modified files

Assisted (recommended). It checks the working copy against the baseline,
refuses to overwrite a file SVN changed on its own, and does steps 2 and 3
together. Without `--execute` it only prints what it would do:

```bash
python3 "$$TOOL" apply --run "$$RUN" --group "$$N" --svn-wc "$$WC" --svn-bin "$$SVN"
python3 "$$TOOL" apply --run "$$RUN" --group "$$N" --svn-wc "$$WC" --svn-bin "$$SVN" --execute
```

By hand:

```bash
cp -R "$$RUN/groups/$$N/payload/." "$$WC/"
```

On Windows, copy the *contents* of `groups\NN\payload` into the working copy and
answer "Replace" for existing files; every file in `payload` is meant to replace.

### 3. Tell SVN about added, deleted and renamed paths

Skip this step if you used the assisted `apply --execute`.

Renames first, so history follows the file. `rename.tsv` lists `old<TAB>new`.
Because step 2 already copied the new file, move it aside, let SVN move the old
one, then put the new content back:

```bash
while IFS=$$'\t' read -r old new; do
  [ -n "$$old" ] || continue
  rm "$$new"
  "$$SVN" move --parents -- "$$old" "$$new"
  cp "$$RUN/groups/$$N/payload/$$new" "$$new"
done < "$$RUN/groups/$$N/rename.tsv"
```

Do not replace a rename with a delete and an add: SVN would treat them as
unrelated files. A rename whose two halves fall in different groups is listed in
`GROUPS.md` under Notes and is transferred as a delete and an add on purpose.

Then additions and deletions. The `svn-targets-*.txt` files already carry the
trailing `@` that SVN needs for names containing `@`:

```bash
if [ -s "$$RUN/groups/$$N/svn-targets-add.txt" ]; then
  "$$SVN" add --parents --targets "$$(native "$$RUN/groups/$$N/svn-targets-add.txt")"
fi
if [ -s "$$RUN/groups/$$N/svn-targets-delete.txt" ]; then
  "$$SVN" delete --targets "$$(native "$$RUN/groups/$$N/svn-targets-delete.txt")"
fi
```

A Windows `svn.exe` converts both its command-line arguments and the contents
of a `--targets` file to the system code page. A file name with characters
outside that code page cannot be given to it either way. For such names use the
assisted apply, which runs svn from inside the file's directory, or a GUI client
such as TortoiseSVN.

Properties: `executable.txt` lists `set<TAB>path` and `unset<TAB>path`.

```bash
"$$SVN" propset svn:executable ON path/to/script     # for each "set"
"$$SVN" propdel svn:executable path/to/file           # for each "unset"
```

`manual.txt` lists what the tool does not transfer (symbolic links, submodules,
Git LFS files), each with the reason. Handle them now or note them for later.

### 4. Inspect what is staged

```bash
"$$SVN" status
"$$SVN" diff --summarize
"$$SVN" diff | less
```

Expect only `A`, `M` and `D` on this group's paths. `?` means a file is not
added, or build output or a dependency slipped in. `!` means an added file is
missing. `C` or `~` means stop.

Compare content with the package. This catches a binary file damaged in copying,
which `svn diff` cannot show:

```bash
( cd "$$WC" && sha256sum -c "$$RUN/groups/$$N/sha256sums.txt" )
```

A text file can fail this check when `svn:eol-style` or `svn:keywords` is set on
it, because SVN then rewrites its bytes in the working copy. Binary files must
always match.

### 5. Check the working copy itself before committing

The checks that made this group `ready` ran on a Git worktree. They do not prove
that the SVN working copy is the same: it can differ in files the transfer never
touched, in line endings, in properties that rewrite content, or in a copying
mistake. So check the copy that will actually be committed:

```bash
python3 "$$TOOL" precommit --run "$$RUN" --group "$$N" --svn-wc "$$WC" --svn-bin "$$SVN"
```

It confirms that only this group is staged, that every transferred file holds
the package content, reports any difference between the working copy and the Git
worktree, and runs the required checks inside the working copy.

$check_step

Commit only when it reports `ready`. On `failed`, do not commit: revert with
`"$$SVN" revert -R .`, find the cause, and fix it. On `blocked` or `unverified`
the checks could not run here; committing is then your decision, and the group
table records it. The checks may leave build output behind as unversioned files:
`precommit` lists them, and they must not be added.

### 6. Commit, yourself

```bash
"$$SVN" status                       # last look
"$$SVN" commit -F "$$(native "$$RUN/groups/$$N/message.txt")"
```

svn prints `Committed revision <REV>.` Keep that number for step 7.

### 7. Record and verify the revision before the next group

```bash
"$$SVN" update
python3 "$$TOOL" record --run "$$RUN" --group "$$N" --revision <REV> --svn-wc "$$WC" --svn-bin "$$SVN"
```

`record` compares what the revision changed, and the working copy's content,
with the group. It reports `verified` or `mismatch`. Without `--svn-wc` it only
stores the number as `user provided`, and the group does not count as
transferred. Do not start the next group on a `mismatch`.

### The same steps with TortoiseSVN

1. Right-click the working copy, **SVN Update**, then **Check for
   modifications**; the list must be empty.
2. Copy the contents of `groups\NN\payload` into the working copy and choose
   **Replace** for existing files.
3. For each line of `rename.tsv`: delete the copied new file, right-drag the old
   file onto its new place and choose **SVN Move versioned item(s) here**, then
   copy the new file from `payload` again. For each line of `delete.txt`:
   right-click the file, **TortoiseSVN > Delete**.
4. Right-click the working copy, **SVN Commit**. Tick **Show unversioned
   files** and tick exactly the files of `add.txt`. Every listed path must
   belong to this group; untick or cancel otherwise.
5. Paste `message.txt` into the message box and press **OK**. The result window
   ends with the committed revision.
6. Record the revision with the `record` command of step 7.

## Groups
$sections

## Revision mapping

| Group | Transfer commit | SVN revision | Status |
|---|---|---|---|
$mapping

This table is regenerated each time a revision is recorded.

## Conditions that need attention

$warnings

## Differences between SVN and Git that were not transferred

$reconcile

## After the last group

```bash
python3 "$$TOOL" verify --run "$$RUN" --svn-wc "$$WC" --svn-bin "$$SVN" --svn-final
```

It confirms that every operation is in exactly one group, that the last
cumulative state equals the Git target within the scope, and that the working
copy holds that state.

## Cleanup, only when you have confirmed the transfer

```bash
python3 "$$TOOL" cleanup --run "$$RUN"             # lists what would be removed
python3 "$$TOOL" cleanup --run "$$RUN" --execute   # removes this run's worktrees and branch
```

By hand:

```bash
git -C "$$REPO" worktree remove "$$RUN/worktrees/group-01"     # repeat per group
git -C "$$REPO" worktree prune
git -C "$$REPO" branch -D "$branch"
```

`git worktree remove` refuses a worktree with unsaved changes; look at them
before forcing anything. The manifest, this guide, `GROUPS.md` and the packages
stay in the run directory.
