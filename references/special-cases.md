# Special cases

Nothing below is skipped silently. Each case is either handled, blocked with a
message, or reported in `inspect`, `GROUPS.md` and the per-group `manual.txt`
with what to do. Pass these on to the user as they are.

## Handled automatically

| Case | Behaviour |
|---|---|
| Binary files | Exported from Git objects byte for byte. `sha256sums.txt` per group lets the user check the working copy. |
| Names with spaces or `@` | Passed without a shell. `svn-targets-*.txt` carry the trailing `@` SVN needs. |
| Deleted files | Listed in `delete.txt`; `apply` schedules `svn delete`. |
| Renames inside one group | Listed in `rename.tsv`; `apply` runs `svn move`, then writes the new content. |
| Executable bit | `apply` sets or removes `svn:executable`. A manual transfer must do it from `executable.txt`. |
| Unicode names on a POSIX svn client | Passed as they are (not tested; see the tested environment). |

## Blocked until the user decides

| Case | Message | What to do |
|---|---|---|
| Baseline not stated | `the baseline is not defined` | Ask the user; never pick one. |
| Working copy has uncommitted changes | `working copy has uncommitted changes` | Commit or revert them in SVN first. |
| Mixed revisions | `working copy is at mixed revisions` | `svn update`. |
| Switched paths | `working copy has switched paths` | Switch back or use another working copy. |
| SVN content differs from the Git base | `does not match the baseline` | SVN has its own change. Port it to Git first, or pick the right base, or exclude the path. It is never overwritten. |
| A file is already in SVN but new in Git | `already versioned in SVN, but new in Git` | Same as above. |
| An unversioned file is in the way | `an unversioned file is in the way` | Remove or move it. |
| Secret-like names, dependency or build directories | `look like secrets, dependencies or build output` | `--exclude` them, or `--allow-flagged <pattern>` when they truly belong in SVN. |
| Path with a line break, or not valid UTF-8 | `unsupported path` | Rename it in Git or exclude it. |
| A path is a file in one group and a directory in another | `plan order is impossible` | Put the change of both in one group. |
| A directory replaced by a file of the same name | `already versioned in SVN, but new in Git` | Not supported in one run: SVN keeps the emptied directory. Remove it in SVN in a run of its own, then transfer the file. |

## Reported; needs a manual step

These are kept in the group commits and worktrees, so the Git side stays true to
the target, but they are left out of the package and listed under manual items.

| Case | Why | What to do |
|---|---|---|
| Symbolic link | SVN stores links with `svn:special`; a Windows client cannot create one. | Recreate the link with a POSIX client, or replace it with a copy. |
| Git submodule | SVN has no gitlink. | Map it to `svn:externals`, or vendor the files. |
| Git LFS file | The repository holds a pointer, not the file. | Copy the real file from a checkout that has LFS installed, then `svn add` it. |

`verify` shows them as `not_run`, and `record` notes that a verified revision
did not check them.

## Reported; a decision about content

| Case | Report | Notes |
|---|---|---|
| Text stored with CRLF in Git | warning `crlf` | Transferred byte for byte. Decide whether SVN should hold LF and whether to set `svn:eol-style`. |
| Mixed line endings | warning `mixed-eol` | `svn:eol-style` cannot be set until the file is normalised. |
| Working copy file equals the Git base except for line endings | note | Accepted as matching, because `svn:eol-style native` produces exactly that on Windows. |
| First import: both sides hold a file with different content | `reconcile` | Not transferred. The user decides which side is right. Line-ending-only differences are marked as such. |
| First import: file exists only in SVN | `svn_only` | Left untouched. |
| `svn:eol-style`, `svn:keywords`, `svn:needs-lock`, `svn:mime-type`, `svn:special` on files the transfer changes | warning `svn-property` | SVN may rewrite the bytes on checkout, so a checksum comparison of a text file can differ. |
| `svn:externals` | warning `svn-externals` | Paths under an external belong to another repository. Exclude them from the scope. |
| Sparse checkout | warning `sparse-checkout` | A directory with a depth other than `infinity` may lack files the transfer expects. `svn update --set-depth infinity`. |
| `.gitattributes`, `.gitmodules` changes | warnings | Git-only behaviour that SVN does not honour. |
| Names a Windows working copy cannot hold | warning `windows-hostile` | Reserved characters, or a trailing space or dot. |
| Paths left out by `--include`/`--exclude` | warning `out-of-scope` | SVN will not receive them. |
| Rename split across groups | note | Transferred as a delete and an add; SVN history does not follow the file. |
| Base is not an ancestor of the target | note | The delta is a plain tree difference. |

## Non-ASCII file names with a Windows svn client

A Windows `svn.exe` converts command-line arguments and `--targets` files to the
system code page. A name with characters outside that code page cannot be given
to it. Verified with TortoiseSVN's command-line client 1.14 on code page 1252.

`apply` works around this where it can do so exactly:

| Operation | Name is ASCII, directory is not | Name itself is non-ASCII |
|---|---|---|
| Copy added or modified content | done | done |
| `svn add` | done, by running svn inside the directory | done with `svn add .` inside the directory, but only when that directory held no other unversioned or ignored file; otherwise reported |
| `svn delete`, `svn propset`, `svn propdel` | done, by running svn inside the directory | **reported, not done** |
| `svn move` | reported, not done | reported, not done |

Reported steps appear in `needs_manual_svn_step`. Do them in a GUI client such
as TortoiseSVN, which does not have the limitation, or with a POSIX client.
`record` will report `mismatch` until the revision really contains them.

Under WSL every non-ASCII name is treated as unpassable, because the client's
code page is not known there. On native Windows the system encoding decides.

## Not supported

- **Splitting one file between groups.** Groups are sets of paths; a file goes
  to one group in its final state.
- **Transferring each Git commit as its own SVN revision.** Use `git svn` for
  that.
- **Removing directories.** Git does not track them, so a directory emptied by
  a group stays in SVN. The user may `svn delete` it; `record` accepts that.
- **Translating `.gitignore` to `svn:ignore`.**
- **Committing.** By design.
