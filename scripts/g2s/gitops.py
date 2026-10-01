"""Git plumbing. Never touches the source repository's index, branch or working tree."""
import os
import subprocess

from .common import G2SError, run

LFS_MARKER = b"version https://git-lfs.github.com/spec/v1"


class Git(object):
    def __init__(self, repo):
        self.repo = os.path.abspath(repo)
        top = run(["git", "-C", self.repo, "rev-parse", "--show-toplevel"], check=False)
        if top.returncode != 0:
            raise G2SError("not a Git repository: %s" % repo)
        self.top = top.stdout.decode("utf-8").strip()

    def git(self, *args, **kw):
        index = kw.pop("index", None)
        env = dict(os.environ)
        env.setdefault("GIT_AUTHOR_NAME", self._identity("user.name", "git-to-svn-transfer"))
        env.setdefault("GIT_AUTHOR_EMAIL", self._identity("user.email", "git-to-svn-transfer@localhost"))
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
        if index:
            env["GIT_INDEX_FILE"] = index
        return run(["git", "-c", "core.quotepath=off"] + list(args), cwd=kw.pop("cwd", self.top),
                   env=env, stdin=kw.pop("stdin", None), check=kw.pop("check", True))

    _identities = {}

    def _identity(self, key, fallback):
        if key not in self._identities:
            proc = run(["git", "-C", self.repo, "config", "--get", key], check=False)
            self._identities[key] = proc.stdout.decode("utf-8").strip() or fallback
        return self._identities[key]

    def out(self, *args, **kw):
        return self.git(*args, **kw).stdout.decode("utf-8").strip()

    def commit_sha(self, ref):
        proc = self.git("rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
        if proc.returncode != 0:
            raise G2SError("Git ref does not resolve to a commit: %s" % ref)
        return proc.stdout.decode().strip()

    def tree_of(self, rev):
        return self.out("rev-parse", "--verify", rev + "^{tree}")

    def object_exists(self, sha):
        return self.git("cat-file", "-e", sha, check=False).returncode == 0

    def empty_tree(self):
        return self.out("mktree", stdin=b"")

    def ls_tree(self, tree):
        """path -> (mode, type, sha) for every entry, recursively."""
        raw = self.git("ls-tree", "-r", "-z", tree).stdout
        entries = {}
        for record in raw.split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, kind, sha = meta.decode().split()
            entries[path.decode("utf-8", "surrogateescape")] = (mode, kind, sha)
        return entries

    def write_tree(self, entries, index):
        """Build a tree from a path -> (mode, type, sha) map using a throwaway index."""
        if os.path.exists(index):
            os.remove(index)
        payload = b"".join(
            ("%s %s %s\t" % entries[path]).encode() + path.encode("utf-8", "surrogateescape") + b"\0"
            for path in sorted(entries))
        self.git("update-index", "-z", "--index-info", index=index, stdin=payload)
        tree = self.out("write-tree", index=index)
        os.remove(index)
        return tree

    def worktree_snapshot_tree(self, index):
        """Tree of HEAD plus every uncommitted and untracked, non-ignored change.

        Uses a private index, so the repository's own index and files stay as they are.
        """
        if os.path.exists(index):
            os.remove(index)
        self.git("read-tree", "HEAD", index=index)
        self.git("add", "-A", "--", ".", index=index)
        tree = self.out("write-tree", index=index)
        os.remove(index)
        return tree

    def diff_raw(self, tree_a, tree_b, renames=False):
        """Changes from tree_a to tree_b as dicts; with renames=True only rename pairs."""
        args = ["diff-tree", "-r", "-z", "--raw", "--no-abbrev"]
        args.append("-M" if renames else "--no-renames")
        fields = self.git(*(args + [tree_a, tree_b])).stdout.split(b"\0")
        result, i = [], 0
        while i < len(fields):
            head = fields[i]
            if not head.startswith(b":"):
                i += 1
                continue
            old_mode, new_mode, old_sha, new_sha, status = head[1:].decode().split()
            path = fields[i + 1].decode("utf-8", "surrogateescape")
            i += 2
            if status[0] in "RC":
                new_path = fields[i].decode("utf-8", "surrogateescape")
                i += 1
                if renames and status[0] == "R":
                    result.append({"from": path, "to": new_path, "similarity": int(status[1:] or 0)})
                continue
            if not renames:
                result.append({"status": status[0], "path": path, "old_mode": old_mode,
                               "new_mode": new_mode, "old_sha": old_sha, "new_sha": new_sha})
        return result

    def read_blobs(self, shas):
        """Yield (sha, bytes) for each blob through one cat-file process."""
        proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=self.top,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for sha in shas:
                proc.stdin.write(sha.encode() + b"\n")
                proc.stdin.flush()
                header = proc.stdout.readline().split()
                if len(header) != 3 or header[1] != b"blob":
                    raise G2SError("expected a blob for %s, got %r" % (sha, header))
                size = int(header[2])
                data = proc.stdout.read(size)
                proc.stdout.read(1)
                yield sha, data
        finally:
            proc.stdin.close()
            proc.stdout.close()
            proc.wait()

    def commit_tree(self, tree, parent, message):
        args = ["commit-tree", tree]
        if parent:
            args += ["-p", parent]
        return self.out(*args, stdin=message.encode("utf-8"))

    def commits_between(self, base, target):
        """Subjects and touched paths per commit, oldest first, to inform grouping."""
        raw = self.git("log", "--reverse", "--no-renames", "--name-only", "-z",
                       "--format=%x01%H%x02%s%x02", base + ".." + target).stdout
        commits = []
        for chunk in raw.split(b"\x01")[1:]:
            sha, subject, rest = chunk.split(b"\x02", 2)
            paths = [p.decode("utf-8", "surrogateescape") for p in rest.strip(b"\n\0").split(b"\0") if p.strip(b"\n")]
            commits.append({"sha": sha.decode(), "subject": subject.decode("utf-8", "replace"),
                            "paths": [p.lstrip("\n") for p in paths]})
        return commits

    def status_porcelain(self, cwd=None, untracked=True):
        mode = "--untracked-files=all" if untracked else "--untracked-files=no"
        return self.git("status", "--porcelain", mode, cwd=cwd or self.top).stdout.decode(
            "utf-8", "replace").splitlines()

    def worktrees(self):
        """Registered worktree path -> HEAD sha."""
        raw = self.out("worktree", "list", "--porcelain")
        result, path = {}, None
        for line in raw.splitlines():
            if line.startswith("worktree "):
                path = os.path.realpath(line[9:])
            elif line.startswith("HEAD ") and path:
                result[path] = line[5:]
        return result

    def branch_sha(self, name):
        proc = self.git("rev-parse", "--verify", "--quiet", "refs/heads/" + name, check=False)
        return proc.stdout.decode().strip() if proc.returncode == 0 else None
