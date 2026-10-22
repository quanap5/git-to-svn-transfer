---
name: visual
description: Draw an SVN repository's history as a git-style graph in one self-contained HTML page. Lanes for trunk, branches and tags, the revision each branch was copied from, merges read from svn:mergeinfo, and for every revision its message, author, date and changed paths. Read-only towards SVN. Use when the user wants to see, visualise, graph or browse SVN history, branches, tags or merges, or asks for something like "git log --graph" for SVN.
license: MIT
compatibility: Needs the git-to-svn-transfer skill installed next to this one (it runs that skill's scripts/git_to_svn.py), Python 3.8+ and an svn command-line client.
metadata:
  version: "1.6.1"
---

# Visual

SVN has no `git log --graph`. This skill draws one: a page the user opens in a
browser, built from `svn log -v` and `svn:mergeinfo`.

## The command

The script lives in the sibling skill: `../git-to-svn-transfer/scripts/git_to_svn.py`
relative to this skill's directory. Call it by absolute path.

```bash
python3 <git-to-svn-transfer>/scripts/git_to_svn.py graph (--svn-wc <dir> | --url <url>) \
  --out <file.html> [--limit 300] [--svn-bin <path>]
```

- `--svn-wc` or `--url` names the repository. Any working copy or any URL
  inside it will do; the whole repository is drawn.
- `--out` must be outside every SVN working copy (the tool refuses otherwise)
  and should be outside Git repositories too, so it is not committed by
  mistake. A good default is the directory the user is working from, or a run
  directory of `git-to-svn-transfer`.
- `--limit` is how many of the newest revisions are drawn. Raise it when the
  user asks for older history; it makes the page larger and slower to build.
- `--svn-bin` names the client when `svn` is not on `PATH`, for example a
  Windows `svn.exe` driven from WSL. `G2S_SVN` works too.

stdout is JSON: `html`, `repository`, `head`, `revisions`, `lanes`, `forks`,
`merges`, `truncated` and `notes`. Exit code 2 means the target is not SVN or
cannot be read; say what failed.

## Steps

1. Find the repository: a working copy the user named, one used earlier in the
   conversation, or a URL. If none is known, ask for it and nothing else.
2. Run `graph`. Building the merge lines reads one property per merge, so a
   remote repository with many merges takes a while.
3. Tell the user, in their language: the repository, the revision range shown,
   how many lanes, forks and merges, every entry of `notes`, and the page path.
4. Tell them how to open it. From WSL with the page on a Windows drive:

   ```bash
   explorer.exe "$(wslpath -w <file.html>)"
   ```

5. Explain the page once: each row is a revision, newest on top; each column is
   a lane (trunk, a branch, or a tag drawn as a diamond); a curve marks where a
   branch or tag was copied from; a dashed line is a merge. Click a row for the
   full message and changed paths. The filter dims rows that do not match.

## What the page cannot show

- Branches outside the standard layout (`trunk`, `branches/<name>`,
  `tags/<name>`, at any depth). A repository without that layout is one lane.
- Diffs. Only the list of changed paths.
- Merges done without `svn:mergeinfo` (very old servers, or merges recorded
  by hand), and merges older than the shown range or beyond the lookup limit;
  `notes` says when the limit was reached.

## Boundaries

- Read-only. The tool runs only `svn info`, `svn log` and `svn propget`. Never
  run any svn command that changes a repository or a working copy for this
  skill.
- Never write the page inside a working copy.
