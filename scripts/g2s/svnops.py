"""Subversion CLI wrapper. Read-only except for the explicit add/delete/move/prop calls.

There is deliberately no commit function: committing is the user's step.
"""
import locale
import os
import shutil
import xml.etree.ElementTree as ET

from .common import G2SError, run

DIRTY_STATES = {"modified", "added", "deleted", "replaced", "conflicted", "missing",
                "obstructed", "incomplete", "merged"}


def find_svn(explicit=None):
    candidate = explicit or os.environ.get("G2S_SVN") or shutil.which("svn") or shutil.which("svn.exe")
    if not candidate:
        raise G2SError("no svn client found; pass --svn-bin or set G2S_SVN to the svn executable")
    if not (os.path.isfile(candidate) or shutil.which(candidate)):
        raise G2SError("svn client not found at: %s" % candidate)
    return candidate


class Svn(object):
    def __init__(self, binary=None):
        self.bin = find_svn(binary)
        # A Windows client driven from a POSIX shell (WSL) needs Windows-style
        # absolute paths and prints backslashes.
        self.windows_client = self.bin.lower().endswith(".exe") and os.name == "posix"

    def native(self, path):
        if not self.windows_client:
            return path
        return run(["wslpath", "-w", path]).stdout.decode("utf-8").strip()

    def _norm(self, path):
        return path.replace("\\", "/") if (self.windows_client or os.name == "nt") else path

    def svn(self, args, cwd, check=True):
        return run([self.bin, "--non-interactive"] + args, cwd=cwd, check=check)

    def _xml(self, args, cwd):
        proc = self.svn(args + ["--xml"], cwd)
        try:
            return ET.fromstring(proc.stdout)
        except ET.ParseError as exc:
            raise G2SError("could not parse svn output for %s: %s" % (args[0], exc))

    @staticmethod
    def peg(path):
        """A trailing @ stops svn reading an @ inside a file name as a peg revision."""
        return path + "@" if "@" in path else path

    def is_working_copy(self, wc):
        return os.path.isdir(wc) and self.svn(["info"], wc, check=False).returncode == 0

    def info(self, wc):
        entry = self._xml(["info"], wc).find("entry")
        wcinfo = entry.find("wc-info")
        root = wcinfo.findtext("wcroot-abspath") if wcinfo is not None else None
        return {
            "url": entry.findtext("url"),
            "relative_url": entry.findtext("relative-url"),
            "repository_root": entry.find("repository").findtext("root"),
            "repository_uuid": entry.find("repository").findtext("uuid"),
            "revision": int(entry.get("revision")),
            "depth": (wcinfo.findtext("depth") if wcinfo is not None else None) or "infinity",
            "is_wc_root": root is not None,
        }

    def status(self, wc):
        """Every item svn knows or sees: path -> {item, props, revision}."""
        root = self._xml(["status", "-v", "--no-ignore"], wc)
        items = {}
        for entry in root.iter("entry"):
            path = self._norm(entry.get("path"))
            wc_status = entry.find("wc-status")
            rev = wc_status.get("revision")
            items[path] = {"item": wc_status.get("item"), "props": wc_status.get("props"),
                           "revision": int(rev) if rev and rev.lstrip("-").isdigit() else None,
                           "switched": wc_status.get("switched") == "true"}
        return items

    def sparse_dirs(self, wc):
        """Directories whose depth is not infinity, and excluded entries."""
        root = self._xml(["info", "-R", "--depth", "infinity"], wc)
        sparse = []
        for entry in root.iter("entry"):
            depth = entry.findtext("wc-info/depth")
            if depth and depth != "infinity":
                sparse.append({"path": self._norm(entry.get("path")), "depth": depth})
        return sparse

    def properties(self, wc):
        """path -> {property: value} for every versioned item that has properties."""
        root = self._xml(["proplist", "-R", "-v"], wc)
        props = {}
        for target in root.iter("target"):
            props[self._norm(target.get("path"))] = {
                p.get("name"): (p.text or "") for p in target.findall("property")}
        return props

    def snapshot(self, wc):
        """Everything prepare, apply and resume need to judge a working copy."""
        if not self.is_working_copy(wc):
            raise G2SError("not an SVN working copy: %s" % wc)
        info = self.info(wc)
        status = self.status(wc)
        revisions = sorted({s["revision"] for p, s in status.items()
                            if s["revision"] is not None and s["revision"] >= 0 and s["item"] != "unversioned"})
        props = self.properties(wc)
        externals = sorted(p for p, values in props.items() if "svn:externals" in values)
        externals += sorted(p for p, s in status.items() if s["item"] == "external" and p not in externals)
        dirty = sorted(p for p, s in status.items()
                       if s["item"] in DIRTY_STATES or s["props"] in ("modified", "conflicted"))
        return {
            "info": info,
            "status": status,
            "properties": props,
            "revision_min": revisions[0] if revisions else info["revision"],
            "revision_max": revisions[-1] if revisions else info["revision"],
            "mixed_revisions": len(revisions) > 1,
            "dirty": dirty,
            "externals": externals,
            "sparse": self.sparse_dirs(wc),
            "switched": sorted(p for p, s in status.items() if s["switched"]),
        }

    def changed_paths(self, wc, revision):
        """What a revision changed, as paths relative to the working copy's URL."""
        info = self.info(wc)
        prefix = info["relative_url"][1:].rstrip("/")  # '^/trunk' -> '/trunk'
        root = self._xml(["log", "-v", "-r", str(int(revision))], wc)
        entry = root.find("logentry")
        if entry is None:
            raise G2SError("revision %s does not exist on %s" % (revision, info["url"]))
        changes = []
        for node in entry.iter("path"):
            full = node.text
            if prefix and not (full == prefix or full.startswith(prefix + "/")):
                changes.append({"path": full, "action": node.get("action"), "kind": node.get("kind"), "outside": True})
                continue
            changes.append({"path": full[len(prefix):].lstrip("/"), "action": node.get("action"),
                            "kind": node.get("kind"), "outside": False,
                            "copyfrom": node.get("copyfrom-path")})
        return {"author": entry.findtext("author"), "message": entry.findtext("msg") or "", "changes": changes}

    # --- naming paths to the client
    #
    # A Windows svn client converts its arguments, and the contents of a --targets
    # file, to the system code page. A name with a character outside that code page
    # arrives mangled, so such a name can never be passed. The current directory is
    # not converted, which leaves two safe routes: run svn inside the parent directory
    # and pass only the file name, or run it inside a directory and pass '.'.

    def addressable(self, text):
        if not (self.windows_client or os.name == "nt"):
            return True
        encoding = "ascii" if self.windows_client else locale.getpreferredencoding(False)
        try:
            text.encode(encoding)
            return True
        except (UnicodeEncodeError, LookupError):
            return False

    def route(self, path):
        """'root' (pass the whole path), 'parent' (pass the name from its directory)
        or 'unnamed' (the client cannot be given this name at all)."""
        if self.addressable(path):
            return "root"
        return "parent" if self.addressable(path.rsplit("/", 1)[-1]) else "unnamed"

    def _batched(self, verb_args, names, cwd):
        batch, size = [], 0
        for name in names:
            arg = self.peg(name)
            if batch and (len(batch) >= 40 or size + len(arg) > 6000):
                self.svn(verb_args + batch, cwd)
                batch, size = [], 0
            batch.append(arg)
            size += len(arg) + 1
        if batch:
            self.svn(verb_args + batch, cwd)

    def _named(self, verb_args, wc, paths):
        """Run a verb over paths the client can be given. Returns the ones it cannot."""
        by_parent, unnamed = {}, []
        self._batched(verb_args, [p for p in paths if self.route(p) == "root"], wc)
        for path in paths:
            route = self.route(path)
            if route == "parent":
                parent, name = path.rsplit("/", 1)
                by_parent.setdefault(parent, []).append(name)
            elif route == "unnamed":
                unnamed.append(path)
        for parent, names in sorted(by_parent.items()):
            self._batched(verb_args, names, os.path.join(wc, *parent.split("/")))
        return unnamed

    def _ensure_versioned_dirs(self, wc, directory, versioned):
        parts = directory.split("/") if directory else []
        for depth in range(1, len(parts) + 1):
            current = "/".join(parts[:depth])
            if current not in versioned:
                self.svn(["add", "--depth", "empty", "."], os.path.join(wc, *parts[:depth]))
                versioned.add(current)

    def add(self, wc, paths, versioned, bulk_ok):
        """Schedule files for addition.

        versioned: paths svn already tracks. bulk_ok: directories that held no
        unversioned file before the copy, where every unversioned file is therefore
        ours and 'svn add .' adds nothing else. Returns the paths left unadded.
        """
        versioned = set(versioned)
        simple = [p for p in paths if self.route(p) == "root"]
        self._batched(["add", "--parents", "--depth", "empty", "--"], simple, wc)
        left, bulk = [], set()
        for path in paths:
            route = self.route(path)
            if route == "root":
                continue
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if route == "unnamed" and parent not in bulk_ok:
                left.append(path)
                continue
            self._ensure_versioned_dirs(wc, parent, versioned)
            cwd = os.path.join(wc, *parent.split("/")) if parent else wc
            if route == "parent":
                self.svn(["add", "--depth", "empty", "--", self.peg(path.rsplit("/", 1)[-1])], cwd)
            elif parent not in bulk:
                self.svn(["add", "--force", "--no-ignore", "--depth", "files", "."], cwd)
                bulk.add(parent)
        return left

    def delete(self, wc, paths):
        return self._named(["delete", "--"], wc, paths)

    def can_move(self, source, dest):
        return self.route(source) == "root" and self.route(dest) == "root"

    def move(self, wc, source, dest):
        self.svn(["move", "--parents", "--", self.peg(source), self.peg(dest)], wc)

    def propset(self, wc, name, value, paths):
        return self._named(["propset", name, value, "--"], wc, paths)

    def propdel(self, wc, name, paths):
        return self._named(["propdel", name, "--"], wc, paths)
