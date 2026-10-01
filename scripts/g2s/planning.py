"""Compute the delta to transfer, detect special cases, and validate a group plan.

The grouping itself is a judgement made by a person or an AI and arrives here as a
structured plan. This module only decides whether that plan is valid and complete.
"""
import hashlib
import os
import re

from .common import (G2SError, check_relpath, flag_reason, is_binary, lf, matches_any,  # noqa: F401
                     sha256_bytes, sha256_file, specificity, windows_hostile)
from .gitops import LFS_MARKER
from .svnops import DIRTY_STATES

KINDS = {"120000": "symlink", "160000": "submodule"}
UNTRACKED = ("unversioned", "ignored", "none", None)


def in_scope(scope, path):
    if scope["include"] and not matches_any(scope["include"], path):
        return False
    return not matches_any(scope["exclude"], path)


def _kind(mode):
    return KINDS.get(mode, "file")


def _eol(data):
    if is_binary(data):
        return "binary"
    crlf = data.count(b"\r\n")
    total = data.count(b"\n")
    if total == 0:
        return "none"
    if crlf == 0:
        return "lf"
    return "crlf" if crlf == total else "mixed"


def compute_delta(git, base_tree, target_tree, scope):
    """Operations that turn base_tree into target_tree, limited to the scope."""
    ops, skipped = [], []
    for change in git.diff_raw(base_tree, target_tree):
        path = change["path"]
        if not in_scope(scope, path):
            skipped.append(path)
            continue
        check_relpath(path)
        status = change["status"]
        op = {"A": "add", "D": "delete"}.get(status, "modify")
        old_kind = _kind(change["old_mode"]) if op != "add" else None
        new_kind = _kind(change["new_mode"]) if op != "delete" else None
        entry = {
            "path": path, "op": op,
            "old_mode": change["old_mode"] if op != "add" else None,
            "new_mode": change["new_mode"] if op != "delete" else None,
            "old_blob": change["old_sha"] if op != "add" else None,
            "new_blob": change["new_sha"] if op != "delete" else None,
            "kind": new_kind or old_kind, "manual": None, "rename_from": None, "rename_to": None,
            "size": None, "sha256": None, "sha256_lf": None, "binary": None, "eol": None,
            "before_sha256": None, "before_sha256_lf": None, "exec": None,
            "flag": flag_reason(path), "windows_hostile": windows_hostile(path),
        }
        if "symlink" in (old_kind, new_kind):
            entry["manual"] = ("symbolic link: SVN stores links through svn:special, which a Windows "
                               "client cannot create; recreate the link or replace it with a copy by hand")
        elif "submodule" in (old_kind, new_kind):
            entry["manual"] = ("Git submodule: SVN has no gitlink; map it to svn:externals or vendor "
                               "the files by hand")
        if op != "delete" and change["new_mode"] == "100755" and change["old_mode"] != "100755":
            entry["exec"] = "set"
        elif op == "modify" and change["old_mode"] == "100755" and change["new_mode"] == "100644":
            entry["exec"] = "unset"
        ops.append(entry)
    _read_content_facts(git, ops)
    renames = [r for r in git.diff_raw(base_tree, target_tree, renames=True)]
    return ops, sorted(skipped), renames


def _read_content_facts(git, ops):
    new = {o["new_blob"]: o for o in ops if o["new_blob"] and o["kind"] == "file" and not o["manual"]}
    wanted = {}
    for o in ops:
        if o["manual"]:
            continue
        if o["new_blob"]:
            wanted.setdefault(o["new_blob"], []).append(("new", o))
        if o["old_blob"]:
            wanted.setdefault(o["old_blob"], []).append(("old", o))
    for sha, data in git.read_blobs(sorted(wanted)):
        digest, digest_lf = sha256_bytes(data), sha256_bytes(lf(data))
        for side, op in wanted[sha]:
            if side == "new":
                op.update(size=len(data), sha256=digest, sha256_lf=digest_lf,
                          binary=is_binary(data), eol=_eol(data))
                if data.startswith(LFS_MARKER):
                    op["manual"] = ("Git LFS pointer: the repository holds a pointer, not the file; "
                                    "copy the real file from a checkout with LFS installed")
            else:
                op.update(before_sha256=digest, before_sha256_lf=digest_lf)
    return new


def collect_warnings(ops, skipped):
    """Conditions the transfer handles only partly. Each is reported, none is dropped."""
    warnings = []

    def note(code, text, paths):
        if paths:
            warnings.append({"code": code, "message": text, "count": len(paths), "paths": sorted(paths)[:200]})

    live = [o for o in ops if o["op"] != "delete"]
    note("crlf", "text files stored with CRLF in Git; they are transferred byte for byte. Decide "
         "whether SVN should hold LF and set svn:eol-style accordingly",
         [o["path"] for o in live if o["eol"] == "crlf"])
    note("mixed-eol", "text files with mixed line endings; svn:eol-style cannot be set on them "
         "until they are normalised", [o["path"] for o in live if o["eol"] == "mixed"])
    note("executable", "files whose executable bit changes; the apply helper sets or clears "
         "svn:executable, a manual transfer must do it by hand",
         [o["path"] for o in ops if o["exec"]])
    note("windows-hostile", "names a Windows working copy cannot hold (reserved characters, or a "
         "trailing space or dot)", [o["path"] for o in ops if o["windows_hostile"]])
    note("gitattributes", ".gitattributes changes may alter line-ending or filter behaviour that SVN "
         "does not honour", [o["path"] for o in ops if o["path"].split("/")[-1] == ".gitattributes"])
    note("gitmodules", ".gitmodules changes: submodules need an SVN counterpart decided by hand",
         [o["path"] for o in ops if o["path"].split("/")[-1] == ".gitmodules"])
    note("out-of-scope", "changed paths left out by --include/--exclude; SVN will not receive them",
         skipped)
    return warnings


def manual_items(ops):
    return [{"path": o["path"], "op": o["op"], "reason": o["manual"]} for o in ops if o["manual"]]


def _git_blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def versioned_files(snapshot, wc):
    return sorted(p for p, s in snapshot["status"].items()
                  if s["item"] not in UNTRACKED and s["item"] != "external" and p != "."
                  and os.path.isfile(os.path.join(wc, *p.split("/"))))


def baseline_from_working_copy(git, wc, snapshot, target_tree, scope):
    """First import: derive the baseline from what SVN already holds.

    A file SVN and the target hold identically is baseline. A file both hold with
    different content is NOT taken from Git: it is reported for the user to
    reconcile, because nothing here can tell which side is right.
    """
    target = git.ls_tree(target_tree)
    baseline, reconcile, svn_only, differing = {}, [], [], {}
    for path in versioned_files(snapshot, wc):
        if not in_scope(scope, path):
            continue
        if path not in target:
            svn_only.append(path)
            continue
        mode, kind, sha = target[path]
        with open(os.path.join(wc, *path.split("/")), "rb") as handle:
            data = handle.read()
        if kind == "blob" and mode != "120000" and _git_blob_sha(data) == sha:
            baseline[path] = target[path]
        elif kind == "blob" and mode != "120000":
            differing[path] = (sha, data)
        else:
            reconcile.append({"path": path, "difference": "type",
                              "detail": "SVN holds a regular file where Git holds a %s" % _kind(mode)})
    blobs = dict(git.read_blobs(sorted({sha for sha, _ in differing.values()})))
    for path, (sha, data) in sorted(differing.items()):
        eol_only = lf(data) == lf(blobs[sha])
        reconcile.append({"path": path, "difference": "line-endings" if eol_only else "content",
                          "detail": "identical apart from line endings" if eol_only
                          else "SVN and Git hold different content"})
    return baseline, reconcile, svn_only


def wc_file_state(full, sha256, sha256_lf):
    """Compare a working-copy file with an expected content hash."""
    if not os.path.lexists(full):
        return "absent"
    if not os.path.isfile(full) or os.path.islink(full):
        return "different"
    if sha256 is None:
        return "unknown"
    if sha256_file(full) == sha256:
        return "same"
    with open(full, "rb") as handle:
        data = handle.read()
    if sha256_lf and not is_binary(data) and sha256_bytes(lf(data)) == sha256_lf:
        return "eol"
    return "different"


def check_pre_state(wc, snapshot, ops):
    """Does the working copy hold what each operation expects to find?

    Returns (problems, notes). A problem means applying would overwrite or lose
    something SVN has that the Git base does not.
    """
    problems, notes = [], []
    status = snapshot["status"]
    for op in ops:
        path = op["path"]
        full = os.path.join(wc, *path.split("/"))
        item = status.get(path, {}).get("item")
        versioned = item not in UNTRACKED
        if op["op"] == "add":
            if versioned:
                problems.append({"path": path, "problem": "already versioned in SVN, but new in Git"})
            elif os.path.lexists(full):
                problems.append({"path": path, "problem": "an unversioned file is in the way"})
            continue
        if not versioned:
            problems.append({"path": path, "problem": "expected in SVN (Git %ss it) but not versioned there" % op["op"]})
            continue
        state = wc_file_state(full, op["before_sha256"], op["before_sha256_lf"])
        if state in ("different", "absent"):
            problems.append({"path": path, "problem": "SVN content differs from the Git base (%s)" % state})
        elif state == "eol":
            notes.append({"path": path, "note": "matches the Git base apart from line endings"})
    return problems, notes


def working_copy_blockers(snapshot):
    """Working-copy conditions that make any transfer unsafe."""
    blockers = []
    if snapshot["dirty"]:
        blockers.append("working copy has uncommitted changes: " + ", ".join(snapshot["dirty"][:10])
                        + (" ..." if len(snapshot["dirty"]) > 10 else ""))
    if snapshot["mixed_revisions"]:
        blockers.append("working copy is at mixed revisions r%d..r%d; run svn update first"
                        % (snapshot["revision_min"], snapshot["revision_max"]))
    if snapshot["switched"]:
        blockers.append("working copy has switched paths: " + ", ".join(snapshot["switched"][:10]))
    return blockers


def working_copy_warnings(snapshot, ops):
    warnings = []
    touched = {o["path"] for o in ops}
    dirs = set()
    for path in touched:
        parts = path.split("/")
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
    for sparse in snapshot["sparse"]:
        where = sparse["path"]
        if where == "." or where in dirs:
            warnings.append({"code": "sparse-checkout", "count": 1, "paths": [where],
                             "message": "working copy directory has depth '%s'; files the transfer "
                             "expects may be absent. Run svn update --set-depth infinity there" % sparse["depth"]})
    if snapshot["externals"]:
        warnings.append({"code": "svn-externals", "count": len(snapshot["externals"]),
                         "paths": snapshot["externals"][:200],
                         "message": "svn:externals are defined; paths under an external belong to "
                         "another repository. Exclude them from the scope and transfer them there"})
    special = {}
    for path, props in snapshot["properties"].items():
        for name in ("svn:eol-style", "svn:keywords", "svn:needs-lock", "svn:special", "svn:mime-type"):
            if name in props and path in touched:
                special.setdefault(name, []).append(path)
    for name, paths in sorted(special.items()):
        warnings.append({"code": "svn-property", "count": len(paths), "paths": sorted(paths)[:200],
                         "message": "%s is set on files the transfer changes; SVN may rewrite their "
                         "bytes on checkout, so a checksum comparison can differ" % name})
    return warnings


KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def validate_plan(plan, ops, renames):
    """Check a group plan against the delta. Returns (groups, notes) or raises with every fault."""
    errors = []
    if not isinstance(plan, dict) or not isinstance(plan.get("groups"), list) or not plan["groups"]:
        raise G2SError("plan must be an object with a non-empty 'groups' list")
    if plan.get("schema_version") != 1:
        errors.append("plan.schema_version must be 1")
    keys, groups = [], []
    for position, raw in enumerate(plan["groups"], start=1):
        label = "group %d" % position
        if not isinstance(raw, dict):
            errors.append("%s is not an object" % label)
            continue
        key = raw.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            errors.append("%s: 'key' must be lowercase letters, digits and hyphens" % label)
        elif key in keys:
            errors.append("%s: duplicate key '%s'" % (label, key))
        for field in ("title", "rationale", "message"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                errors.append("%s: '%s' is required" % (label, field))
        paths = raw.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            errors.append("%s: 'paths' must be a non-empty list of patterns" % label)
            paths = []
        depends = raw.get("depends_on", [])
        if not isinstance(depends, list):
            errors.append("%s: 'depends_on' must be a list" % label)
            depends = []
        for dep in depends:
            if dep not in keys:
                errors.append("%s: depends on '%s', which is not an earlier group" % (label, dep))
        keys.append(key)
        groups.append({"id": "%02d" % position, "key": key, "title": raw.get("title"),
                       "rationale": raw.get("rationale"), "message": raw.get("message"),
                       "depends_on": list(depends), "patterns": list(paths), "ops": []})
    if errors:
        raise G2SError("invalid plan:\n  - " + "\n  - ".join(errors))

    owner, used = {}, set()
    for op in ops:
        best, winners = None, []
        for index, group in enumerate(groups):
            for pattern in group["patterns"]:
                score = specificity(pattern, op["path"])
                if score is None:
                    continue
                if best is None or score > best:
                    best, winners = score, [(index, pattern)]
                elif score == best and index not in [w[0] for w in winners]:
                    winners.append((index, pattern))
        if not winners:
            errors.append("unassigned: %s" % op["path"])
        elif len(winners) > 1:
            errors.append("ambiguous: %s is claimed equally by %s" % (
                op["path"], ", ".join("%s (%s)" % (groups[i]["key"], p) for i, p in winners)))
        else:
            index, pattern = winners[0]
            owner[op["path"]] = index
            used.add((index, pattern))
            groups[index]["ops"].append(op)
    for index, group in enumerate(groups):
        if not group["ops"]:
            errors.append("group '%s' matches no changed path" % group["key"])
        for pattern in group["patterns"]:
            if group["ops"] and (index, pattern) not in used and not any(
                    specificity(pattern, o["path"]) is not None for o in ops):
                errors.append("group '%s': pattern '%s' matches no changed path" % (group["key"], pattern))
    if errors:
        shown = errors[:60] + (["... and %d more" % (len(errors) - 60)] if len(errors) > 60 else [])
        raise G2SError("plan does not partition the delta:\n  - " + "\n  - ".join(shown))

    notes = []
    by_path = {o["path"]: o for o in ops}
    for pair in renames:
        src, dst = by_path.get(pair["from"]), by_path.get(pair["to"])
        if not src or not dst:
            notes.append("rename %s -> %s crosses the transfer scope; handled as separate operations"
                         % (pair["from"], pair["to"]))
            continue
        if src["op"] != "delete" or dst["op"] != "add" or src["manual"] or dst["manual"]:
            continue
        if owner[src["path"]] != owner[dst["path"]]:
            notes.append("rename %s -> %s is split across groups '%s' and '%s'; handled as a delete "
                         "and an add, so SVN history does not follow it" % (
                             pair["from"], pair["to"], groups[owner[src["path"]]]["key"],
                             groups[owner[dst["path"]]]["key"]))
            continue
        src["rename_to"], dst["rename_from"] = dst["path"], src["path"]
        dst["similarity"] = pair["similarity"]
    return groups, notes
