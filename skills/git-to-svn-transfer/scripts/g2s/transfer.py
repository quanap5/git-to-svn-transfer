"""The run lifecycle: analyse, prepare, resume, verify, apply, record, cleanup."""
import datetime
import hashlib
import json
import os
import shutil
import tempfile

from . import SCHEMA_VERSION, TOOL_VERSION
from . import checks as checks_mod
from . import planning
from . import progress
from .common import (G2SError, inside, matches_any, now_iso, read_json, sha256_file,
                     wc_path, write_json, write_text)
from .gitops import Git
from .render import render_documents
from .svnops import Svn

MANIFEST = "manifest.json"
REVISION_STATES = ("verified", "user_provided", "mismatch")


# ---------------------------------------------------------------- analysis

def _mode(opts):
    chosen = [name for name, on in (("incremental", bool(opts.get("base"))),
                                    ("initial-svn-wc", opts.get("initial_from_svn_wc")),
                                    ("initial-empty", opts.get("initial_empty"))) if on]
    if len(chosen) != 1:
        raise G2SError(
            "the baseline is not defined. Pass exactly one of:\n"
            "  --base <git-ref>         incremental sync: SVN already holds that Git state\n"
            "  --initial-from-svn-wc    first import: derive the baseline from the SVN working copy\n"
            "  --initial-empty          first import into an SVN location with nothing in scope")
    return chosen[0]


def analyse(opts, index):
    """Everything known before a plan exists. Creates no refs, worktrees or files."""
    git = Git(opts["repo"])
    mode = _mode(opts)
    scope = {"include": list(opts.get("include") or []), "exclude": list(opts.get("exclude") or [])}
    target_ref = opts.get("target") or "HEAD"
    target_sha = git.commit_sha(target_ref)
    uncommitted = bool(opts.get("include_uncommitted"))
    dirty = git.status_porcelain()
    if uncommitted:
        if target_sha != git.commit_sha("HEAD"):
            raise G2SError("--include-uncommitted needs the target to be HEAD, because uncommitted "
                           "changes are relative to the checked-out commit")
        target_tree = git.worktree_snapshot_tree(index)
    else:
        target_tree = git.tree_of(target_sha)

    svn = snapshot = None
    wc = opts.get("svn_wc")
    if wc:
        wc = os.path.abspath(wc)
        svn = Svn(opts.get("svn_bin"))
        snapshot = svn.snapshot(wc)
        if inside(wc, git.top) or inside(git.top, wc):
            raise G2SError("the SVN working copy and the Git repository must be separate directories")
    elif mode == "initial-svn-wc":
        raise G2SError("--initial-from-svn-wc needs --svn-wc")

    reconcile, svn_only, notes = [], [], []
    base = {"ref": opts.get("base"), "sha": None, "tree": None}
    if mode == "incremental":
        base["sha"] = git.commit_sha(opts["base"])
        base["tree"] = git.tree_of(base["sha"])
        if git.git("merge-base", "--is-ancestor", base["sha"], target_sha, check=False).returncode != 0:
            notes.append("base %s is not an ancestor of target %s; the delta is a plain tree difference"
                         % (base["sha"][:10], target_sha[:10]))
    elif mode == "initial-empty":
        base["tree"] = git.empty_tree()
    else:
        entries, reconcile, svn_only = planning.baseline_from_working_copy(
            git, wc, snapshot, target_tree, scope)
        base["tree"] = git.write_tree(entries, index) if entries else git.empty_tree()

    ops, skipped, renames = planning.compute_delta(git, base["tree"], target_tree, scope)
    held_back = {r["path"] for r in reconcile}
    ops = [o for o in ops if o["path"] not in held_back]
    warnings = planning.collect_warnings(ops, skipped)

    blockers, baseline_mismatch, baseline_notes = [], [], []
    svn_baseline = None
    if snapshot:
        blockers += planning.working_copy_blockers(snapshot)
        warnings += planning.working_copy_warnings(snapshot, ops)
        baseline_mismatch, baseline_notes = planning.check_pre_state(
            wc, snapshot, [o for o in ops if not o["manual"]])
        if baseline_mismatch:
            blockers.append("the SVN working copy does not match the baseline for %d path(s): %s"
                            % (len(baseline_mismatch), "; ".join(
                                "%s (%s)" % (m["path"], m["problem"]) for m in baseline_mismatch[:8])))
        info = snapshot["info"]
        svn_baseline = {"source": "working-copy", "working_copy": wc, "url": info["url"],
                        "relative_url": info["relative_url"], "repository_root": info["repository_root"],
                        "repository_uuid": info["repository_uuid"], "revision": snapshot["revision_max"],
                        "checked_at": now_iso()}
    elif opts.get("svn_url") or opts.get("svn_revision") is not None:
        svn_baseline = {"source": "user-provided, not verified", "working_copy": None,
                        "url": opts.get("svn_url"), "revision": opts.get("svn_revision"),
                        "checked_at": None}
    elif mode == "incremental":
        notes.append("no SVN working copy was given, so nothing confirms that SVN holds the Git base")

    allow = list(opts.get("allow_flagged") or [])
    flagged = [o for o in ops if o["flag"] and o["op"] != "delete" and not matches_any(allow, o["path"])]
    if flagged:
        blockers.append("%d path(s) look like secrets, dependencies or build output: %s. Exclude them "
                        "with --exclude, or name them with --allow-flagged if they belong in SVN"
                        % (len(flagged), "; ".join("%s [%s]" % (o["path"], o["flag"]) for o in flagged[:8])))
    if not ops:
        blockers.append("nothing to transfer: base and target are identical within the scope")

    return {
        "git": git, "svn": svn, "mode": mode, "scope": scope, "allow_flagged": allow,
        "base": base, "target": {"ref": target_ref, "sha": target_sha, "tree": target_tree,
                                 "include_uncommitted": uncommitted},
        "source_dirty": bool(dirty), "ops": ops, "renames": renames, "skipped": skipped,
        "warnings": warnings, "notes": notes, "blockers": blockers,
        "manual_items": planning.manual_items(ops), "reconcile": reconcile, "svn_only": svn_only,
        "baseline_mismatch": baseline_mismatch, "baseline_notes": baseline_notes,
        "svn_baseline": svn_baseline, "svn_wc": wc,
    }


def inspect(opts):
    """Facts for whoever proposes the grouping. Read-only."""
    tmp = tempfile.mkdtemp(prefix="g2s-inspect-")
    try:
        a = analyse(opts, os.path.join(tmp, "index"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    git = a["git"]
    by_dir = {}
    for op in a["ops"]:
        parts = op["path"].split("/")
        key = "/".join(parts[:2]) + "/" if len(parts) > 2 else (parts[0] + "/" if len(parts) == 2 else "(root)")
        slot = by_dir.setdefault(key, {"add": 0, "modify": 0, "delete": 0})
        slot[op["op"]] += 1
    commits = []
    if a["mode"] == "incremental":
        commits = git.commits_between(a["base"]["sha"], a["target"]["sha"])
        wanted = {o["path"] for o in a["ops"]}
        for commit in commits:
            commit["paths"] = [p for p in commit["paths"] if p in wanted][:60]
        commits = [c for c in commits if c["paths"]][-400:]
    counts = {k: sum(1 for o in a["ops"] if o["op"] == k) for k in ("add", "modify", "delete")}
    return {
        "mode": a["mode"], "repository": git.top, "base": a["base"], "target": a["target"],
        "scope": a["scope"], "desired_groups": opts.get("groups") or 10,
        "source_has_uncommitted_changes": a["source_dirty"],
        "svn_baseline": a["svn_baseline"], "counts": counts, "renames": a["renames"],
        "by_directory": by_dir, "commits": commits,
        "operations": [{k: o[k] for k in ("path", "op", "kind", "size", "binary", "eol", "exec",
                                           "manual", "flag")} for o in a["ops"]],
        "warnings": a["warnings"], "notes": a["notes"], "manual_items": a["manual_items"],
        "reconcile": a["reconcile"], "svn_only": a["svn_only"],
        "baseline_mismatch": a["baseline_mismatch"], "blockers": a["blockers"],
        "ready_to_prepare": not a["blockers"],
    }


# ---------------------------------------------------------------- prepare

def _fingerprint(a, groups, checks):
    material = json.dumps([a["mode"], a["base"]["tree"], a["target"]["tree"], a["scope"],
                           [[g["key"], g["title"], g["message"], g["patterns"], g["depends_on"]]
                            for g in groups], checks], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _existing_runs(out):
    if not os.path.isdir(out):
        return
    for name in sorted(os.listdir(out)):
        path = os.path.join(out, name, MANIFEST)
        if os.path.isfile(path):
            try:
                yield os.path.join(out, name), read_json(path)
            except (ValueError, OSError):
                continue


def tree_conflicts(git, base_tree, groups):
    """A path cannot be a file in one cumulative state and a directory in the same one."""
    entries = set(git.ls_tree(base_tree))
    problems = []
    for group in groups:
        for op in group["ops"]:
            (entries.discard if op["op"] == "delete" else entries.add)(op["path"])
        for path in sorted(entries):
            parts = path.split("/")
            for depth in range(1, len(parts)):
                if "/".join(parts[:depth]) in entries:
                    problems.append("after group '%s', '%s' is both a file and the directory of '%s'; "
                                    "put the change of both in one group" % (
                                        group["key"], "/".join(parts[:depth]), path))
    return problems


def prepare(opts, stop_after=None):
    out = os.path.abspath(opts["out"])
    git_probe = Git(opts["repo"])
    if inside(out, git_probe.top):
        raise G2SError("--out must be outside the source repository (%s)" % git_probe.top)
    if opts.get("svn_wc") and inside(out, os.path.abspath(opts["svn_wc"])):
        raise G2SError("--out must be outside the SVN working copy")
    plan = read_json(opts["plan"])
    progress.plan(5)
    progress.step("analyse the change set and validate the plan")
    os.makedirs(out, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="g2s-", dir=out)
    try:
        a = analyse(opts, os.path.join(scratch, "index"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if a["blockers"]:
        raise G2SError("cannot prepare:\n  - " + "\n  - ".join(a["blockers"]))
    groups, rename_notes = planning.validate_plan(plan, a["ops"], a["renames"])
    conflicts = tree_conflicts(a["git"], a["base"]["tree"], groups)
    if conflicts:
        raise G2SError("plan order is impossible:\n  - " + "\n  - ".join(conflicts[:20]))

    checks = checks_mod.parse_checks(plan, [g["key"] for g in groups], opts.get("required_check") or [],
                                     opts.get("check_each") or [], opts.get("check") or [])
    superseded = None
    if opts.get("supersedes"):
        superseded = os.path.abspath(opts["supersedes"])
        if _load(superseded)["repository"] != a["git"].top:
            raise G2SError("--supersedes names a run of a different repository: %s" % superseded)
    fingerprint = _fingerprint(a, groups, checks)
    for run_dir, old in _existing_runs(out):
        if old.get("fingerprint") == fingerprint:
            raise G2SError("this exact transfer is already prepared in %s (state: %s). Continue it with "
                           "'resume --run %s' instead of preparing it again" % (run_dir, old.get("state"), run_dir))

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = "%s-%s" % (stamp, a["target"]["sha"][:8])
    run_dir, n = os.path.join(out, run_id), 1
    while os.path.exists(run_dir):
        n += 1
        run_dir = os.path.join(out, "%s-%d" % (run_id, n))
    run_id = os.path.basename(run_dir)
    os.makedirs(run_dir)
    progress.item("%d group(s), %d check(s), run %s" % (len(groups), len(checks), run_id), "done")
    write_json(os.path.join(run_dir, "plan.json"), plan)

    manifest = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION, "run_id": run_id,
        "created_at": now_iso(), "state": "planned", "mode": a["mode"], "fingerprint": fingerprint,
        "repository": a["git"].top, "base": a["base"], "target": a["target"], "scope": a["scope"],
        "allow_flagged": a["allow_flagged"], "desired_groups": opts.get("groups") or 10,
        "svn_baseline": a["svn_baseline"],
        "svn_client": ({"binary": a["svn"].bin, "windows_client_under_posix": a["svn"].windows_client}
                       if a["svn"] else None),
        "branch": "svn-transfer/" + run_id,
        "baseline_commit": None, "scoped_target_tree": None,
        "groups": [{
            "id": g["id"], "key": g["key"], "title": g["title"], "rationale": g["rationale"],
            "depends_on": g["depends_on"], "patterns": g["patterns"], "message": g["message"],
            "commit": None, "tree": None, "worktree": os.path.join("worktrees", "group-" + g["id"]),
            "package": os.path.join("groups", g["id"]), "packaged": False,
            "operations": g["ops"],
            "readiness": {"status": "unverified", "reasons": ["required checks have not been run"], "at": None},
            "svn_precommit": None,
            "svn_revision": {"revision": None, "status": None, "recorded_at": None, "details": []},
        } for g in groups],
        "supersedes": superseded, "superseded_by": None,
        "manual_items": a["manual_items"], "reconcile": a["reconcile"], "svn_only": a["svn_only"],
        "warnings": a["warnings"], "notes": a["notes"] + rename_notes + [
            "%s: %s" % (n["path"], n["note"]) for n in a["baseline_notes"]],
        "out_of_scope": a["skipped"], "check_commands": checks, "check_results": [],
        "verification": None, "resume_log": [], "cleanup": None,
    }
    write_json(os.path.join(run_dir, MANIFEST), manifest)
    materialize(run_dir, stop_after=stop_after)
    if superseded:
        old = _load(superseded)
        old["superseded_by"] = run_dir
        _save(superseded, old)
        render_documents(superseded, old)
    return run_dir


def _load(run_dir):
    path = os.path.join(run_dir, MANIFEST)
    if not os.path.isfile(path):
        raise G2SError("no %s in %s; --run must name a run directory" % (MANIFEST, run_dir))
    manifest = read_json(path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise G2SError("manifest schema %r is not supported by this version (%d)"
                       % (manifest.get("schema_version"), SCHEMA_VERSION))
    for group in manifest["groups"]:
        group.setdefault("readiness", {"status": "unverified", "at": None,
                                       "reasons": ["required checks have not been run"]})
        group.setdefault("svn_precommit", None)
    return manifest


def _svn(manifest, svn_bin):
    """The client named now, else the one the run was prepared with."""
    return Svn(svn_bin or (manifest.get("svn_client") or {}).get("binary"))


def _save(run_dir, manifest):
    write_json(os.path.join(run_dir, MANIFEST), manifest)


def _apply_ops(entries, ops):
    for op in ops:
        if op["op"] == "delete":
            entries.pop(op["path"], None)
        else:
            kind = "commit" if op["new_mode"] == "160000" else "blob"
            entries[op["path"]] = (op["new_mode"], kind, op["new_blob"])


def materialize(run_dir, stop_after=None):
    """Create whatever the manifest describes and does not exist yet. Safe to repeat."""
    run_dir = os.path.abspath(run_dir)
    manifest = _load(run_dir)
    git = Git(manifest["repository"])
    index = os.path.join(run_dir, "index.tmp")
    log = []

    if not manifest["baseline_commit"]:
        if manifest["mode"] == "incremental":
            manifest["baseline_commit"] = manifest["base"]["sha"]
        else:
            manifest["baseline_commit"] = git.commit_tree(
                manifest["base"]["tree"], None,
                "SVN baseline for transfer run %s\n\nNot for transfer: the state the groups are measured against.\n"
                % manifest["run_id"])
        _save(run_dir, manifest)

    progress.step("cumulative commits on %s" % manifest["branch"])
    entries = git.ls_tree(manifest["base"]["tree"])
    parent = manifest["baseline_commit"]
    for group in manifest["groups"]:
        _apply_ops(entries, group["operations"])
        made = not (group["commit"] and git.object_exists(group["commit"]))
        if made:
            group["tree"] = git.write_tree(entries, index)
            group["commit"] = git.commit_tree(group["tree"], parent, group["message"].rstrip("\n") + "\n")
            log.append("created commit for group %s" % group["id"])
            _save(run_dir, manifest)
        progress.item("group %s %s" % (group["id"], group["key"]), "created" if made else "exists",
                      "%s, %d operation(s)" % (group["commit"][:10], len(group["operations"])))
        parent = group["commit"]
    manifest["scoped_target_tree"] = git.write_tree(entries, index)
    final = manifest["groups"][-1]["commit"]
    existing = git.branch_sha(manifest["branch"])
    if existing is None:
        git.git("branch", manifest["branch"], final)
        log.append("created branch %s" % manifest["branch"])
    elif existing != final:
        raise G2SError("branch %s exists and points at %s, not at this run's final commit %s; it was "
                       "changed outside this run" % (manifest["branch"], existing[:10], final[:10]))
    _save(run_dir, manifest)
    if stop_after == "commits":
        return manifest

    progress.step("one worktree per group")
    registered = git.worktrees()
    for group in manifest["groups"]:
        path = os.path.join(run_dir, group["worktree"])
        real = os.path.realpath(path)
        if real in registered:
            progress.item("group %s" % group["id"], "exists", group["worktree"])
            continue
        if os.path.exists(path) and os.listdir(path):
            raise G2SError("%s exists but is not a worktree of this repository; it will not be overwritten" % path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        progress.running("group %s: checking out" % group["id"])
        git.git("worktree", "add", "--detach", path, group["commit"])
        log.append("created worktree for group %s" % group["id"])
        progress.item("group %s" % group["id"], "created", group["worktree"])
        if stop_after == "worktree:" + group["id"]:
            _save(run_dir, manifest)
            return manifest

    progress.step("delta packages and commit messages")
    for group in manifest["groups"]:
        repaired = _write_package(run_dir, git, group)
        if repaired:
            log.append("rewrote %d package file(s) for group %s" % (repaired, group["id"]))
        progress.item("group %s" % group["id"], "done", group["package"])
        group["packaged"] = True
        _save(run_dir, manifest)
        if stop_after == "package:" + group["id"]:
            return manifest

    manifest["state"] = "prepared"
    if log and manifest.get("prepared_at"):
        manifest["resume_log"].append({"at": now_iso(), "actions": log})
    manifest.setdefault("prepared_at", now_iso())
    _save(run_dir, manifest)
    progress.step("guides")
    render_documents(run_dir, manifest)
    progress.item("GROUPS.md, SVN_MANUAL_TRANSFER.md", "done")
    return manifest


def _write_package(run_dir, git, group):
    """Export the group's delta. Bytes come from Git objects, so no checkout filter,
    line-ending conversion or LFS smudge can alter them."""
    root = os.path.join(run_dir, group["package"])
    payload = os.path.join(root, "payload")
    ops = group["operations"]
    copy = [o for o in ops if o["op"] != "delete" and not o["manual"]]
    written = 0
    pending = {}
    for op in copy:
        dest = os.path.join(payload, *op["path"].split("/"))
        if os.path.isfile(dest) and sha256_file(dest) == op["sha256"]:
            continue
        pending.setdefault(op["new_blob"], []).append(dest)
    for sha, data in git.read_blobs(sorted(pending)):
        for dest in pending[sha]:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as handle:
                handle.write(data)
            written += 1

    def lines(items):
        return "".join(item + "\n" for item in items)

    auto = [o for o in ops if not o["manual"]]
    adds = [o["path"] for o in auto if o["op"] == "add" and not o["rename_from"]]
    modifies = [o["path"] for o in auto if o["op"] == "modify"]
    deletes = [o["path"] for o in auto if o["op"] == "delete" and not o["rename_to"]]
    renames = [(o["rename_from"], o["path"]) for o in auto if o["rename_from"]]
    write_text(os.path.join(root, "add.txt"), lines(adds))
    write_text(os.path.join(root, "modify.txt"), lines(modifies))
    write_text(os.path.join(root, "delete.txt"), lines(deletes))
    write_text(os.path.join(root, "rename.tsv"), lines("%s\t%s" % pair for pair in renames))
    write_text(os.path.join(root, "svn-targets-add.txt"), lines(Svn.peg(p) for p in adds))
    write_text(os.path.join(root, "svn-targets-delete.txt"), lines(Svn.peg(p) for p in deletes))
    write_text(os.path.join(root, "executable.txt"), lines(
        "%s\t%s" % (o["exec"], o["path"]) for o in auto if o["exec"]))
    write_text(os.path.join(root, "manual.txt"), lines(
        "%s\t%s\t%s" % (o["op"], o["path"], o["manual"]) for o in ops if o["manual"]))
    write_text(os.path.join(root, "sha256sums.txt"), lines(
        "%s  %s" % (o["sha256"], o["path"]) for o in copy))
    write_text(os.path.join(root, "message.txt"), group["message"].rstrip("\n") + "\n")
    write_json(os.path.join(root, "operations.json"), ops)
    return written if group.get("packaged") else 0


# ---------------------------------------------------------------- drift, resume

def drift(run_dir, manifest, svn_wc=None, svn_bin=None):
    """What changed since the run was prepared. Each finding is {area, severity, detail}."""
    git = Git(manifest["repository"])
    found = []

    def add(area, severity, detail):
        found.append({"area": area, "severity": severity, "detail": detail})

    for side in ("base", "target"):
        ref, sha = manifest[side]["ref"], manifest[side]["sha"]
        if not ref or not sha:
            continue
        proc = git.git("rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
        now = proc.stdout.decode().strip() if proc.returncode == 0 else None
        if now != sha:
            add(side, "info", "%s ref '%s' now resolves to %s; this run stays pinned to %s. Start a new "
                "run to transfer newer changes" % (side, ref, (now or "nothing")[:10], sha[:10]))
    if manifest["target"]["include_uncommitted"]:
        scratch = os.path.join(run_dir, "index.tmp")
        if git.commit_sha("HEAD") != manifest["target"]["sha"] or \
                git.worktree_snapshot_tree(scratch) != manifest["target"]["tree"]:
            add("target", "info", "the source working tree changed after its uncommitted changes were "
                "captured; the run still holds the captured state")
    registered = git.worktrees()
    for group in manifest["groups"]:
        path = os.path.join(run_dir, group["worktree"])
        head = registered.get(os.path.realpath(path))
        if (manifest.get("cleanup") or {}).get("worktrees_removed") and head is None:
            continue
        if head is None:
            add("worktree", "missing", "group %s worktree is not registered: %s" % (group["id"], path))
        elif group["commit"] and head != group["commit"]:
            add("worktree", "changed", "group %s worktree HEAD is %s, expected %s" % (
                group["id"], head[:10], group["commit"][:10]))
        elif git.status_porcelain(cwd=path, untracked=False):
            add("worktree", "changed", "group %s worktree has local changes: %s" % (group["id"], path))
        elif git.status_porcelain(cwd=path):
            # Typically output left by a project check. The tracked files are intact.
            add("worktree", "info", "group %s worktree holds untracked files (often build or test "
                "output); cleanup will refuse it until they are removed: %s" % (group["id"], path))
        if group["packaged"]:
            payload = os.path.join(run_dir, group["package"], "payload")
            for op in group["operations"]:
                if op["op"] == "delete" or op["manual"]:
                    continue
                dest = os.path.join(payload, *op["path"].split("/"))
                if not os.path.isfile(dest):
                    add("package", "missing", "group %s package lacks %s" % (group["id"], op["path"]))
                elif sha256_file(dest) != op["sha256"]:
                    add("package", "changed", "group %s package file was altered: %s" % (group["id"], op["path"]))
    wc = svn_wc or (manifest.get("svn_baseline") or {}).get("working_copy")
    if wc and os.path.isdir(wc):
        snapshot = _svn(manifest, svn_bin).snapshot(wc)
        base = manifest.get("svn_baseline") or {}
        if base.get("repository_uuid") and snapshot["info"]["repository_uuid"] != base["repository_uuid"]:
            add("svn", "changed", "working copy belongs to a different repository than the baseline")
        recorded = [g["svn_revision"]["revision"] for g in manifest["groups"] if g["svn_revision"]["revision"]]
        expected = max(recorded + [base.get("revision") or 0])
        if base.get("revision") is not None and snapshot["revision_max"] != expected:
            add("svn", "info", "working copy is at r%d; the last revision this run knows is r%d"
                % (snapshot["revision_max"], expected))
        for blocker in planning.working_copy_blockers(snapshot):
            add("svn", "changed", blocker)
    return found


def resume(run_dir, svn_wc=None, svn_bin=None):
    """Report drift, then finish whatever the earlier prepare did not."""
    run_dir = os.path.abspath(run_dir)
    manifest = _load(run_dir)
    cleaned = bool((manifest.get("cleanup") or {}).get("worktrees_removed"))
    progress.plan(1 if cleaned else 6)
    progress.step("drift since the run was prepared")
    before = drift(run_dir, manifest, svn_wc, svn_bin)
    for found in before:
        progress.item("%s: %s" % (found["area"], found["detail"]), found["severity"])
    if not before:
        progress.item("nothing moved", "same")
    blocking = [d for d in before if d["severity"] == "changed" and d["area"] in ("worktree",)]
    if blocking:
        raise G2SError("resume refuses to continue over changed results:\n  - " + "\n  - ".join(
            d["detail"] for d in blocking) + "\nRestore those worktrees or start a new run.")
    was, logged = manifest["state"], len(manifest["resume_log"])
    if not cleaned:
        manifest = materialize(run_dir)
        progress.step("drift after resuming")
    after = [] if cleaned else drift(run_dir, manifest, svn_wc, svn_bin)
    for found in after:
        progress.item("%s: %s" % (found["area"], found["detail"]), found["severity"])
    if not cleaned and not after:
        progress.item("nothing moved", "same")
    actions = [a for entry in manifest["resume_log"][logged:] for a in entry["actions"]]
    if was != "prepared" and manifest["state"] == "prepared":
        actions.insert(0, "completed an interrupted prepare")
    return {"run": run_dir, "state_before": was, "state": manifest["state"],
            "drift_before": before, "drift_after": after, "actions": actions,
            "groups": [{"id": g["id"], "key": g["key"], "svn_revision": g["svn_revision"]}
                       for g in manifest["groups"]]}


# ---------------------------------------------------------------- verify

def verify(run_dir, svn_wc=None, svn_bin=None, run_checks=False, svn_final=False, timeout=None):
    run_dir = os.path.abspath(run_dir)
    manifest = _load(run_dir)
    git = Git(manifest["repository"])
    results = []

    def record(name, status, detail="", show=True):
        results.append({"check": name, "status": status, "detail": detail})
        if show:
            progress.item(name, status, detail)

    progress.plan(5 if run_checks else 4)
    progress.step("structure: every operation in exactly one group")
    if manifest["state"] != "prepared":
        record("prepare completed", "failed", "state is '%s'; run resume" % manifest["state"])
        return _finish_verify(run_dir, manifest, results)

    # 1. Every delta operation appears in exactly one group.
    index = os.path.join(run_dir, "index.tmp")
    target_tree = manifest["target"]["tree"]
    expected_ops, _, _ = planning.compute_delta(git, manifest["base"]["tree"], target_tree, manifest["scope"])
    held = {r["path"] for r in manifest["reconcile"]}
    expected = {(o["path"], o["op"], o["new_blob"], o["new_mode"]) for o in expected_ops if o["path"] not in held}
    seen, duplicates = set(), []
    for group in manifest["groups"]:
        for op in group["operations"]:
            key = (op["path"], op["op"], op["new_blob"], op["new_mode"])
            if op["path"] in {s[0] for s in seen}:
                duplicates.append(op["path"])
            seen.add(key)
    missing, extra = expected - seen, seen - expected
    record("no operation missing", "failed" if missing else "passed",
           ", ".join(sorted(m[0] for m in missing)[:20]))
    record("no operation repeated or invented", "failed" if (extra or duplicates) else "passed",
           ", ".join(sorted({e[0] for e in extra} | set(duplicates))[:20]))

    # 2. The commit chain is linear and each step changes exactly its group's paths.
    parent, chain_ok, chain_detail = manifest["baseline_commit"], True, []
    for group in manifest["groups"]:
        commit = group["commit"]
        if not commit or not git.object_exists(commit):
            chain_ok = False
            chain_detail.append("group %s commit is missing" % group["id"])
            break
        parents = git.out("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if parents != [parent]:
            chain_ok = False
            chain_detail.append("group %s parent is %s" % (group["id"], ",".join(p[:10] for p in parents)))
        changed = {c["path"] for c in git.diff_raw(git.tree_of(parent), git.tree_of(commit))}
        declared = {o["path"] for o in group["operations"]}
        if changed != declared:
            chain_ok = False
            chain_detail.append("group %s commit changes %d path(s) but declares %d" % (
                group["id"], len(changed), len(declared)))
        parent = commit
    record("commit chain matches the groups", "passed" if chain_ok else "failed", "; ".join(chain_detail))

    # 3. The last cumulative state is the target, inside the scope.
    entries = git.ls_tree(manifest["base"]["tree"])
    target_entries = git.ls_tree(target_tree)
    for op in expected_ops:
        if op["path"] in held:
            continue
        if op["op"] == "delete":
            entries.pop(op["path"], None)
        else:
            entries[op["path"]] = target_entries[op["path"]]
    scoped = git.write_tree(entries, index)
    final_tree = git.tree_of(manifest["groups"][-1]["commit"]) if chain_ok else None
    record("final snapshot equals the target within scope",
           "passed" if final_tree == scoped else "failed",
           "" if final_tree == scoped else "final tree %s, expected %s" % (str(final_tree)[:10], scoped[:10]))
    if not manifest["scope"]["include"] and not manifest["scope"]["exclude"] and not held:
        record("final snapshot equals the whole target tree",
               "passed" if final_tree == target_tree else "failed")

    # 4. Worktrees and packages are what prepare produced.
    progress.step("worktrees and packages")
    cleaned = bool((manifest.get("cleanup") or {}).get("worktrees_removed"))
    found = drift(run_dir, manifest, svn_wc, svn_bin)
    for area in ("worktree", "package"):
        bad = [d["detail"] for d in found if d["area"] == area and d["severity"] != "info"]
        if area == "worktree" and cleaned:
            record("worktrees intact", "not_run", "worktrees were removed by cleanup")
        else:
            record("%ss intact" % area, "failed" if bad else "passed", "; ".join(bad[:10]))
    for item in found:
        if item["severity"] == "info":
            record("drift: " + item["area"], "passed", item["detail"])

    # 5. Things the tool does not transfer are named, not hidden.
    if manifest["manual_items"]:
        record("manual items", "not_run", "%d path(s) need manual handling: %s" % (
            len(manifest["manual_items"]), ", ".join(m["path"] for m in manifest["manual_items"][:10])))
    if manifest["reconcile"]:
        record("SVN/Git differences to reconcile", "not_run", "%d path(s) differ between SVN and Git and "
               "are not transferred: %s" % (len(manifest["reconcile"]),
                                            ", ".join(r["path"] for r in manifest["reconcile"][:10])))

    # 6. SVN side.
    progress.step("SVN working copy")
    wc = svn_wc or (manifest.get("svn_baseline") or {}).get("working_copy")
    if wc and os.path.isdir(wc):
        svn_bad = [d["detail"] for d in found if d["area"] == "svn" and d["severity"] != "info"]
        record("SVN working copy clean and single-revision", "failed" if svn_bad else "passed", "; ".join(svn_bad))
        if svn_final:
            differing = []
            for group in manifest["groups"]:
                for op in group["operations"]:
                    if op["manual"]:
                        continue
                    full = os.path.join(wc, *op["path"].split("/"))
                    if op["op"] == "delete":
                        # A directory at that path means the file went and a directory replaced it.
                        if os.path.lexists(full) and not os.path.isdir(full):
                            differing.append(op["path"] + " (still present)")
                        continue
                    state = planning.wc_file_state(full, op["sha256"], op["sha256_lf"])
                    if state not in ("same", "eol"):
                        differing.append("%s (%s)" % (op["path"], state))
            record("SVN working copy holds the final state", "failed" if differing else "passed",
                   ", ".join(differing[:20]))
    else:
        record("SVN working copy", "not_run", "no working copy given; pass --svn-wc")
    if svn_final and not (wc and os.path.isdir(wc)):
        record("SVN working copy holds the final state", "not_run", "no working copy given")

    unverified = [g["id"] for g in manifest["groups"] if g["svn_revision"]["status"] != "verified"]
    record("every group has a verified SVN revision", "not_run" if unverified else "passed",
           "not yet verified: " + ", ".join(unverified) if unverified else "")

    # 7. Project checks, run on every cumulative worktree. A green build proves that
    # state runs; it does not prove the transfer is complete, which is what 1-3 are for.
    groups, checks = manifest["groups"], manifest["check_commands"]
    if run_checks:
        progress.step("project checks on every cumulative worktree")
    if run_checks and not cleaned:
        collected = []
        for position, group in enumerate(groups):
            outcome = checks_mod.run_for_state(
                checks, position, groups, os.path.join(run_dir, group["worktree"]),
                os.path.join(run_dir, "checks"), "group-%s" % group["id"], timeout)
            for item in outcome:
                item["log"] = os.path.relpath(item["log"], run_dir)
            expected = sum(1 for c in checks if c["required"] and checks_mod.applies(c, position, groups))
            status, reasons = checks_mod.readiness(outcome, expected)
            group["readiness"] = {"status": status, "reasons": reasons, "at": now_iso()}
            collected += outcome
        manifest["check_results"] = collected
    elif run_checks:
        record("project checks", "not_run", "worktrees were removed by cleanup")
    progress.step("readiness")
    for group in groups:
        state = group["readiness"]
        progress.item("group %s %s" % (group["id"], group["key"]), state["status"], "; ".join(state["reasons"]))
        record("group %s readiness: %s" % (group["id"], state["status"]),
               {"ready": "passed", "failed": "failed"}.get(state["status"], "not_run"),
               "; ".join(state["reasons"]), show=False)
    for item in manifest["check_results"]:
        if not item["required"] and item["status"] != "passed":
            record("optional check '%s' [group %s]" % (item["name"], item["group"]), "not_run",
                   "%s (%s); it does not gate readiness, log %s" % (item["status"], item["reason"], item["log"]))
    return _finish_verify(run_dir, manifest, results)


def _finish_verify(run_dir, manifest, results):
    summary = {s: sum(1 for r in results if r["status"] == s) for s in ("passed", "failed", "not_run")}
    states = {g["id"]: g["readiness"]["status"] for g in manifest["groups"]}
    structural_failure = any(r["status"] == "failed" and not r["check"].startswith("group ") for r in results)
    manifest["verification"] = {
        "at": now_iso(), "summary": summary, "readiness": states,
        # Ready means every group earned it; a run with one blocked group is not ready.
        "run_ready": not structural_failure and all(s == "ready" for s in states.values()),
        "results": results}
    _save(run_dir, manifest)
    if manifest["state"] == "prepared":
        render_documents(run_dir, manifest)
    return manifest["verification"]


# ---------------------------------------------------------------- apply

def _group(manifest, group_id):
    wanted = str(group_id).zfill(2)
    for position, group in enumerate(manifest["groups"]):
        if group["id"] == wanted or group["key"] == group_id:
            return position, group
    raise G2SError("no group '%s' in this run; groups are %s" % (
        group_id, ", ".join(g["id"] for g in manifest["groups"])))


def apply_group(run_dir, group_id, svn_wc, svn_bin=None, execute=False, allow_unverified_previous=False,
                allow_not_ready=False):
    """Stage one group in an SVN working copy. Never commits. Dry run unless execute=True."""
    run_dir = os.path.abspath(run_dir)
    wc = os.path.abspath(svn_wc)
    manifest = _load(run_dir)
    if manifest["state"] != "prepared":
        raise G2SError("run is not fully prepared (state '%s'); run resume first" % manifest["state"])
    if manifest.get("superseded_by"):
        raise G2SError("this run was superseded by %s; apply from that run" % manifest["superseded_by"])
    position, group = _group(manifest, group_id)
    progress.plan(3)
    progress.step("preconditions for group %s %s" % (group["id"], group["key"]))
    progress.item("readiness on the Git worktree", group["readiness"]["status"])
    svn = _svn(manifest, svn_bin)
    snapshot = svn.snapshot(wc)
    if inside(run_dir, wc) or inside(wc, manifest["repository"]):
        raise G2SError("the SVN working copy must be separate from the run directory and the Git repository")
    base = manifest.get("svn_baseline") or {}
    if base.get("repository_uuid") and base["repository_uuid"] != snapshot["info"]["repository_uuid"]:
        raise G2SError("this working copy belongs to repository %s, the run was prepared against %s"
                       % (snapshot["info"]["repository_uuid"], base["repository_uuid"]))

    problems = planning.working_copy_blockers(snapshot)
    earlier = [g for g in manifest["groups"][:position] if g["svn_revision"]["status"] != "verified"]
    if earlier and not allow_unverified_previous:
        problems.append("earlier group(s) %s have no verified SVN revision; commit them and run 'record' "
                        "first, or pass --allow-unverified-previous" % ", ".join(g["id"] for g in earlier))
    state = group["readiness"]
    if state["status"] == "failed":
        problems.append("group %s failed a required check on its cumulative worktree (%s). Move its boundary "
                        "or merge it with the group it depends on, then prepare again; a failed group is "
                        "never applied" % (group["id"], "; ".join(state["reasons"])))
    elif state["status"] != "ready" and not allow_not_ready:
        problems.append("group %s is '%s', not ready (%s). Run 'verify --run-checks'; if the checks cannot "
                        "run here, pass --allow-not-ready only on the user's decision"
                        % (group["id"], state["status"], "; ".join(state["reasons"])))
    ops = [o for o in group["operations"] if not o["manual"]]
    payload = os.path.join(run_dir, group["package"], "payload")
    for op in ops:
        wc_path(wc, op["path"])
        if op["op"] != "delete":
            source = os.path.join(payload, *op["path"].split("/"))
            if not os.path.isfile(source) or sha256_file(source) != op["sha256"]:
                problems.append("package file is missing or altered: %s" % op["path"])
    mismatch, notes = planning.check_pre_state(wc, snapshot, ops)
    problems += ["%s: %s" % (m["path"], m["problem"]) for m in mismatch]
    if problems:
        raise G2SError("group %s cannot be applied to %s:\n  - %s" % (group["id"], wc, "\n  - ".join(problems)))

    # Decide, before touching anything, which svn operations this client can be asked
    # to do. What it cannot do is left undone and reported; it is never approximated.
    status = snapshot["status"]
    versioned = {p for p, s in status.items() if s["item"] not in planning.UNTRACKED}
    untracked_dirs = {p.rsplit("/", 1)[0] if "/" in p else "" for p, s in status.items()
                      if s["item"] in ("unversioned", "ignored")}
    unnamed_reason = ("this svn client cannot be given the file name (characters outside its code "
                      "page); do this step in a GUI client such as TortoiseSVN or with a POSIX svn client")
    by_hand = []

    def parent_of(path):
        return path.rsplit("/", 1)[0] if "/" in path else ""

    def bulk_safe(parent):
        full = os.path.join(wc, *parent.split("/")) if parent else wc
        return (parent in versioned or parent == "") and parent not in untracked_dirs \
            if os.path.lexists(full) else True

    renames = []
    for op in ops:
        if not op["rename_from"]:
            continue
        if svn.can_move(op["rename_from"], op["path"]):
            renames.append((op["rename_from"], op["path"]))
        else:
            by_hand.append({"path": op["path"], "action": "svn move %s -> %s, then copy the new content "
                            "from the package" % (op["rename_from"], op["path"]), "reason": unnamed_reason})
    skipped = {entry["path"] for entry in by_hand}
    copies = [o for o in ops if o["op"] != "delete" and o["path"] not in skipped]
    adds, deletes = [], []
    for op in ops:
        path = op["path"]
        if op["op"] == "add" and not op["rename_from"]:
            if svn.route(path) == "unnamed" and not bulk_safe(parent_of(path)):
                by_hand.append({"path": path, "action": "svn add (the file is copied, not added)",
                                "reason": unnamed_reason + "; its directory also holds other unversioned "
                                "files, so adding the directory's files in bulk is not safe"})
            else:
                adds.append(path)
        elif op["op"] == "delete" and not op["rename_to"]:
            if svn.route(path) == "unnamed":
                by_hand.append({"path": path, "action": "svn delete", "reason": unnamed_reason})
            else:
                deletes.append(path)
    exec_set, exec_unset = [], []
    for op in ops:
        if op["exec"] and op["path"] not in skipped:
            if svn.route(op["path"]) == "unnamed":
                by_hand.append({"path": op["path"], "action": "svn prop%s svn:executable" % (
                    "set" if op["exec"] == "set" else "del"), "reason": unnamed_reason})
            else:
                (exec_set if op["exec"] == "set" else exec_unset).append(op["path"])
    bulk_ok = {parent_of(p) for p in adds if svn.route(p) == "unnamed"}
    actions = (["svn move %s -> %s" % pair for pair in renames]
               + ["copy %s" % o["path"] for o in copies]
               + ["svn add %s" % p for p in adds] + ["svn delete %s" % p for p in deletes]
               + ["svn propset svn:executable %s" % p for p in exec_set]
               + ["svn propdel svn:executable %s" % p for p in exec_unset])
    new_dirs = set()
    for path in adds + [dest for _, dest in renames]:
        parts = path.split("/")
        new_dirs.update("/".join(parts[:i]) for i in range(1, len(parts))
                        if "/".join(parts[:i]) not in versioned)
    report = {"group": group["id"], "working_copy": wc, "executed": execute,
              "copies_into": wc, "actions": actions,
              "directories_created_and_added": sorted(new_dirs),
              "needs_manual_svn_step": by_hand,
              "notes": ["%s: %s" % (n["path"], n["note"]) for n in notes],
              "manual": [{"path": o["path"], "op": o["op"], "reason": o["manual"]}
                         for o in group["operations"] if o["manual"]],
              "working_copy_revision": snapshot["revision_max"], "readiness": state["status"],
              "next_step": "run 'precommit' for this group in the working copy before committing: checks "
                           "that passed in the Git worktree do not prove the SVN copy is the same",
              "message_file": os.path.join(run_dir, group["package"], "message.txt")}
    progress.item("working copy at r%s matches the expected state" % snapshot["revision_max"], "passed")
    progress.step("plan the svn operations")
    progress.item("%d action(s): %d move, %d copy, %d add, %d delete" % (
        len(actions), len(renames), len(copies), len(adds), len(deletes)), "done")
    if by_hand:
        progress.item("%d step(s) this svn client cannot do" % len(by_hand), "manual")
    progress.step("stage in the working copy")
    if not execute:
        progress.item("dry run: nothing was changed; pass --execute to stage", "skipped")
    if base.get("revision") is not None and position == 0 and snapshot["revision_max"] != base["revision"]:
        report["notes"].append("working copy is at r%d, the baseline was recorded at r%d; the files this "
                               "group touches still match the baseline" % (snapshot["revision_max"], base["revision"]))
    if not execute:
        return report

    for source, dest in renames:
        svn.move(wc, source, dest)
    # Deletes come before copies: a file replaced by a directory of the same name
    # must be gone before the directory can be created.
    leftover = svn.delete(wc, deletes) if deletes else []
    for op in copies:
        dest = wc_path(wc, op["path"])
        if os.path.islink(dest):
            raise G2SError("refusing to write through a link in the working copy: %s" % op["path"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(os.path.join(payload, *op["path"].split("/")), dest)
    versioned.update(dest for _, dest in renames)
    versioned.difference_update(deletes)
    leftover += svn.add(wc, adds, versioned, bulk_ok) if adds else []
    leftover += svn.propset(wc, "svn:executable", "ON", exec_set) if exec_set else []
    leftover += svn.propdel(wc, "svn:executable", exec_unset) if exec_unset else []

    after = svn.status(wc)
    wrong = ["%s: the svn client could not be given this name" % p for p in leftover]
    for op in copies:
        if sha256_file(wc_path(wc, op["path"])) != op["sha256"]:
            wrong.append("%s: content differs from the package after copying" % op["path"])
    touched = {o["path"] for o in ops} - {entry["path"] for entry in by_hand}
    for path, state in after.items():
        if state["item"] == "unversioned" and path in touched:
            wrong.append("%s: still unversioned" % path)
    for path in deletes:
        # 'replaced' is a file deleted and a directory of the same name added.
        if after.get(path, {}).get("item") not in ("deleted", "replaced"):
            wrong.append("%s: not scheduled for deletion" % path)
    report["post_check"] = {"status": "failed" if wrong else "passed", "problems": wrong}
    progress.item("staged, not committed", report["post_check"]["status"], "; ".join(wrong[:5]))
    report["staged"] = sorted("%s %s" % (s["item"], p) for p, s in after.items()
                              if s["item"] in ("added", "deleted", "modified", "replaced"))
    return report


# ---------------------------------------------------------------- record

def record_revision(run_dir, group_id, revision, svn_wc=None, svn_bin=None, replace=False):
    """Note the revision the user committed. 'verified' is earned by evidence, never by entry."""
    run_dir = os.path.abspath(run_dir)
    manifest = _load(run_dir)
    _, group = _group(manifest, group_id)
    try:
        revision = int(str(revision).lstrip("rR"))
    except ValueError:
        raise G2SError("revision must be a number, got %r" % (revision,))
    current = group["svn_revision"]
    if current["revision"] not in (None, revision) and not replace:
        raise G2SError("group %s already has r%s recorded (%s); pass --replace to change it"
                       % (group["id"], current["revision"], current["status"]))
    for other in manifest["groups"]:
        if other is not group and other["svn_revision"]["revision"] == revision:
            raise G2SError("r%d is already recorded for group %s" % (revision, other["id"]))

    status, details = "user_provided", []
    if svn_wc:
        wc = os.path.abspath(svn_wc)
        svn = _svn(manifest, svn_bin)
        snapshot = svn.snapshot(wc)
        log = svn.changed_paths(wc, revision)
        ops = [o for o in group["operations"] if not o["manual"]]
        manual = {o["path"] for o in group["operations"] if o["manual"]}
        expected = {o["path"]: {"add": "A", "modify": "M", "delete": "D"}[o["op"]] for o in ops}
        actual, removed_dirs = {}, []
        deleted = [p for p, action in expected.items() if action == "D"]
        for change in log["changes"]:
            if change["outside"]:
                details.append("revision also changes a path outside the working copy URL: %s" % change["path"])
                continue
            is_dir = change["kind"] == "dir" or os.path.isdir(os.path.join(wc, *change["path"].split("/"))) \
                if change["path"] else True
            if is_dir and change["action"] in ("A", "M"):
                continue
            # Git tracks no directories, so removing one that the group emptied is the
            # user's call and counts as deleting the files under it.
            if change["kind"] == "dir" and change["action"] == "D" and any(
                    p.startswith(change["path"] + "/") for p in deleted):
                removed_dirs.append(change["path"] + "/")
                continue
            actual[change["path"]] = change["action"]
        for path, action in sorted(expected.items()):
            got = actual.get(path)
            if got is None and action == "D" and any(path.startswith(d) for d in removed_dirs):
                continue
            if got is None:
                details.append("expected %s %s, but the revision does not touch it" % (action, path))
            elif got != action and got != "R":
                details.append("expected %s %s, the revision has %s" % (action, path, got))
        for path, action in sorted(actual.items()):
            if path not in expected and path not in manual:
                details.append("revision changes a path outside this group: %s %s" % (action, path))
        if snapshot["revision_max"] < revision:
            details.append("working copy is at r%d; run svn update so its content can be compared with r%d"
                           % (snapshot["revision_max"], revision))
        elif snapshot["dirty"]:
            details.append("working copy has uncommitted changes, so its content cannot stand for r%d" % revision)
        else:
            for op in ops:
                full = os.path.join(wc, *op["path"].split("/"))
                if op["op"] == "delete":
                    if os.path.lexists(full) and not os.path.isdir(full):
                        details.append("%s should be deleted but is present" % op["path"])
                    continue
                state = planning.wc_file_state(full, op["sha256"], op["sha256_lf"])
                if state not in ("same", "eol"):
                    details.append("%s content is %s compared with the package" % (op["path"], state))
        status = "mismatch" if details else "verified"
        if status == "verified" and manual:
            details.append("%d manual item(s) were not checked: %s" % (len(manual), ", ".join(sorted(manual))))
    else:
        details.append("no working copy given; the revision is recorded as stated and not checked")
    before_commit = (group.get("svn_precommit") or {}).get("status")
    if before_commit != "ready":
        details.append("the working copy was not shown ready before this commit (precommit: %s)"
                       % (before_commit or "not run"))
    group["svn_revision"] = {"revision": revision, "status": status, "recorded_at": now_iso(), "details": details}
    _save(run_dir, manifest)
    render_documents(run_dir, manifest)
    return {"group": group["id"], "revision": revision, "status": status, "details": details}


# ---------------------------------------------------------------- precommit

def precommit(run_dir, group_id, svn_wc, svn_bin=None, skip_checks=False, timeout=None):
    """Check a group where it will be committed from: the SVN working copy itself.

    Checks that passed in a Git worktree say nothing certain about the working copy.
    It can differ in files the transfer never touched, in line endings, in properties
    that rewrite content, and in whatever a manual copy got wrong.
    """
    run_dir = os.path.abspath(run_dir)
    wc = os.path.abspath(svn_wc)
    manifest = _load(run_dir)
    position, group = _group(manifest, group_id)
    git = Git(manifest["repository"])
    svn = _svn(manifest, svn_bin)
    snapshot = svn.snapshot(wc)
    status = snapshot["status"]
    problems, notes = [], []
    progress.plan(4)

    def stage_result(label, seen):
        progress.item(label, "failed" if len(problems) > seen else "passed", "; ".join(problems[seen:seen + 5]))

    # 1. What is staged must be this group and nothing else.
    progress.step("staged paths belong to group %s %s" % (group["id"], group["key"]))
    own = {o["path"] for o in group["operations"]}
    staged = {p: s["item"] for p, s in status.items()
              if s["item"] in planning.DIRTY_STATES or s["props"] in ("modified", "conflicted")}
    if not staged:
        problems.append("nothing is staged in the working copy; apply the group first")
    for path, item in sorted(staged.items()):
        if item in ("conflicted", "missing", "obstructed", "incomplete"):
            problems.append("%s is %s" % (path, item))
        elif path not in own and not any(p.startswith(path + "/") for p in own):
            problems.append("%s is staged (%s) but does not belong to group %s" % (path, item, group["id"]))

    stage_result("%d staged path(s)" % len(staged), 0)

    # 2. Every path transferred so far holds the package content.
    progress.step("transferred files match the packages")
    seen = len(problems)
    transferred = set()
    for earlier in manifest["groups"][:position + 1]:
        for op in earlier["operations"]:
            transferred.add(op["path"])
            if op["manual"]:
                notes.append("%s is a manual item and was not compared" % op["path"])
                continue
            full = os.path.join(wc, *op["path"].split("/"))
            if op["op"] == "delete":
                if os.path.lexists(full) and not os.path.isdir(full):
                    problems.append("%s should be deleted but is present" % op["path"])
                continue
            state = planning.wc_file_state(full, op["sha256"], op["sha256_lf"])
            if state == "eol":
                notes.append("%s matches apart from line endings" % op["path"])
            elif state != "same":
                problems.append("%s is %s compared with the package" % (op["path"], state))
            elif status.get(op["path"], {}).get("item") in planning.UNTRACKED:
                problems.append("%s is copied but not added to SVN" % op["path"])

    stage_result("%d transferred path(s)" % len(transferred), seen)

    # 3. Everything else: is the working copy the same tree the worktree was checked on?
    progress.step("working copy compared with the Git worktree")
    tree = git.ls_tree(group["tree"])
    outside, candidates = [], {}
    for path, (mode, kind, sha) in sorted(tree.items()):
        if path in transferred or kind != "blob" or mode == "120000":
            continue
        full = os.path.join(wc, *path.split("/"))
        if not os.path.isfile(full):
            outside.append({"path": path, "difference": "missing in SVN"})
            continue
        with open(full, "rb") as handle:
            data = handle.read()
        if planning._git_blob_sha(data) != sha:
            candidates[path] = (sha, data)
    blobs = dict(git.read_blobs(sorted({sha for sha, _ in candidates.values()})))
    for path, (sha, data) in sorted(candidates.items()):
        same_text = planning.lf(data) == planning.lf(blobs[sha])
        outside.append({"path": path, "difference": "line endings" if same_text else "content"})
    svn_only = [p for p in planning.versioned_files(snapshot, wc) if p not in tree and p not in transferred]
    identical = not outside and not svn_only
    progress.item("%d difference(s) outside the transfer, %d SVN-only file(s)" % (len(outside), len(svn_only)),
                  "same" if identical else "manual")

    # 4. The project's own checks, run in the working copy.
    progress.step("project checks in the working copy")
    results, created = [], []
    expected = sum(1 for c in manifest["check_commands"]
                   if c["required"] and checks_mod.applies(c, position, manifest["groups"]))
    if problems:
        state, reasons = checks_mod.FAILED, list(problems)
    elif skip_checks:
        state, reasons = checks_mod.UNVERIFIED, ["checks were skipped in the working copy"]
    else:
        results = checks_mod.run_for_state(manifest["check_commands"], position, manifest["groups"], wc,
                                           os.path.join(run_dir, "checks"), "svn-group-%s" % group["id"], timeout)
        for item in results:
            item["log"] = os.path.relpath(item["log"], run_dir)
        after = svn.status(wc)
        created = sorted(p for p, s in after.items()
                         if s["item"] == "unversioned" and status.get(p, {}).get("item") != "unversioned")
        touched = sorted(p for p, s in after.items()
                         if s["item"] in planning.DIRTY_STATES and staged.get(p) != s["item"])
        state, reasons = checks_mod.readiness(results, expected)
        if touched:
            state = checks_mod.FAILED
            reasons = ["a check changed versioned files in the working copy: %s" % ", ".join(touched[:10])] + reasons
    if state == checks_mod.READY and not identical:
        notes.append("the working copy differs from the Git worktree outside the transferred paths; the "
                     "checks above ran on the working copy as it is, which is what will be committed")
    report = {
        "group": group["id"], "working_copy": wc, "status": state, "reasons": reasons, "at": now_iso(),
        "working_copy_revision": snapshot["revision_max"], "staged": sorted("%s %s" % (i, p) for p, i in staged.items()),
        "identical_to_git_worktree": identical, "differences_outside_transfer": outside[:200],
        "svn_only_files": svn_only[:200], "checks": results, "notes": notes,
        "unversioned_files_created_by_checks": created,
        "before_commit": ("Do not add or commit the files the checks created." if created else ""),
    }
    progress.item("group %s in the working copy" % group["id"], state, "; ".join(reasons[:5]))
    group["svn_precommit"] = report
    _save(run_dir, manifest)
    render_documents(run_dir, manifest)
    return report


# ---------------------------------------------------------------- cleanup

def cleanup(run_dir, execute=False, discard_untracked=False):
    """List, and only with execute=True remove, the worktrees and branch this run created."""
    run_dir = os.path.abspath(run_dir)
    manifest = _load(run_dir)
    git = Git(manifest["repository"])
    registered = git.worktrees()
    area = os.path.join(run_dir, "worktrees")
    plan, refused = [], []
    progress.plan(2)
    progress.step("what this run created")
    for group in manifest["groups"]:
        path = os.path.join(run_dir, group["worktree"])
        real = os.path.realpath(path)
        if real not in registered:
            continue
        if not inside(path, area):
            refused.append("%s is outside this run's worktree area" % path)
        elif registered[real] != group["commit"]:
            refused.append("%s HEAD moved to %s; not this run's commit" % (path, registered[real][:10]))
        elif git.status_porcelain(cwd=path) and discard_untracked and not git.status_porcelain(cwd=path, untracked=False):
            # Only untracked files, typically output of the checks: removable on request.
            plan.append({"kind": "worktree", "path": path, "group": group["id"], "discards_untracked": True})
        elif git.status_porcelain(cwd=path):
            names = [line[3:] for line in git.status_porcelain(cwd=path)]
            refused.append("%s has unsaved changes or untracked files (%s%s); if they are only build or "
                           "test output, run cleanup again with --discard-untracked" % (
                               path, ", ".join(names[:5]), " ..." if len(names) > 5 else ""))
        else:
            plan.append({"kind": "worktree", "path": path, "group": group["id"]})
    final = manifest["groups"][-1]["commit"]
    branch_now = git.branch_sha(manifest["branch"])
    if branch_now is not None:
        if branch_now == final:
            plan.append({"kind": "branch", "name": manifest["branch"]})
        else:
            refused.append("branch %s now points at %s, not at this run's final commit" % (
                manifest["branch"], branch_now[:10]))
    report = {"run": run_dir, "executed": execute, "would_remove" if not execute else "removed": plan,
              "refused": refused,
              "kept": [MANIFEST, "GROUPS.md", "SVN_MANUAL_TRANSFER.md", "groups/", "plan.json"]}
    for entry in plan:
        progress.item("%s %s" % (entry["kind"], entry.get("group") or entry.get("name")), "exists")
    for reason in refused:
        progress.item(reason, "refused")
    progress.step("remove")
    if not execute:
        progress.item("dry run: nothing was removed; pass --execute to remove", "skipped")
        return report
    done = []
    for item in plan:
        if item["kind"] == "worktree":
            git.git(*(["worktree", "remove"] + (["--force"] if item.get("discards_untracked") else [])
                      + [item["path"]]))
        elif not refused:
            git.git("branch", "-D", item["name"])
        else:
            report["refused"].append("branch %s kept because a worktree could not be removed" % item["name"])
            progress.item("branch %s" % item["name"], "refused", "a worktree could not be removed")
            continue
        done.append(item)
        progress.item("%s %s" % (item["kind"], item.get("group") or item.get("name")), "removed")
    report["removed"] = done
    remaining = [g for g in manifest["groups"]
                 if os.path.realpath(os.path.join(run_dir, g["worktree"])) in git.worktrees()]
    manifest["cleanup"] = {"at": now_iso(), "removed": done, "refused": report["refused"],
                           "worktrees_removed": not remaining}
    _save(run_dir, manifest)
    return report
