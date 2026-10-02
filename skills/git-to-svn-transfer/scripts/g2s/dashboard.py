"""dashboard.html and events.jsonl: a readable view of a run.

Both are derived from manifest.json and can be rebuilt at any time; nothing reads
them back to decide what to transfer.
"""
import json
import os

from .common import G2SError, now_iso, read_json, write_text
from .gitops import Git
from .render import ASSETS, _base_text, _baseline, _counts, _mode_text

DASHBOARD = "dashboard.html"
EVENTS = "events.jsonl"
MAX_TREE_PATHS = 100000
MAX_LISTED = 200


def log_event(run_dir, command, exit_code, summary="", group=None):
    """Append one line per command to the run's history."""
    event = {"at": now_iso(), "command": command, "exit_code": exit_code, "summary": summary}
    if group:
        event["group"] = str(group)
    with open(os.path.join(run_dir, EVENTS), "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(run_dir):
    path = os.path.join(run_dir, EVENTS)
    if not os.path.isfile(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue  # a half-written last line after an interruption
    return events


def embed(value):
    """JSON that is safe inside a <script> element: no '<' can close it early."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _base_paths(manifest):
    """Files of the baseline tree, so the page can show each cumulative worktree."""
    try:
        paths = sorted(Git(manifest["repository"]).ls_tree(manifest["base"]["tree"]))
    except (G2SError, OSError):
        return None
    return paths if len(paths) <= MAX_TREE_PATHS else None


def _group_checks(manifest, group):
    found = [dict(item, where="Git worktree") for item in manifest["check_results"] if item["group"] == group["id"]]
    found += [dict(item, where="SVN working copy") for item in (group.get("svn_precommit") or {}).get("checks", [])]
    return [{"where": c["where"], "name": c["name"], "kind": c["kind"], "required": c["required"],
             "status": c["status"], "reason": c["reason"], "log": c["log"].replace(os.sep, "/")} for c in found]


def build(run_dir, manifest):
    groups = []
    for group in manifest["groups"]:
        pre = group.get("svn_precommit")
        groups.append({
            "id": group["id"], "key": group["key"], "title": group["title"], "rationale": group["rationale"],
            "depends_on": group["depends_on"], "commit": group["commit"],
            "worktree": group["worktree"].replace(os.sep, "/"), "package": group["package"].replace(os.sep, "/"),
            "message": group["message"], "counts": _counts(group), "readiness": group["readiness"],
            "precommit": {"status": pre["status"], "reasons": pre["reasons"], "at": pre["at"]} if pre else None,
            "revision": group["svn_revision"],
            "ops": [{"p": o["path"], "o": o["op"], "from": o["rename_from"], "to": o["rename_to"],
                     "bin": bool(o["binary"]), "manual": o["manual"]} for o in group["operations"]],
            "checks": _group_checks(manifest, group),
        })
    totals = {key: sum(g["counts"][key] for g in groups) for key in ("add", "modify", "delete", "rename", "manual")}
    totals["ready"] = sum(1 for g in groups if g["readiness"]["status"] == "ready")
    totals["verified"] = sum(1 for g in groups if g["revision"]["status"] == "verified")
    verification = manifest.get("verification")
    return {
        "generated_at": now_iso(),
        "run": {"id": manifest["run_id"], "dir": run_dir, "state": manifest["state"], "mode": _mode_text(manifest),
                "repository": manifest["repository"], "branch": manifest["branch"], "base": _base_text(manifest),
                "target": "%s (%s)" % (manifest["target"]["sha"], manifest["target"]["ref"]),
                "svn_baseline": _baseline(manifest), "superseded_by": manifest.get("superseded_by"),
                "worktrees_removed": bool((manifest.get("cleanup") or {}).get("worktrees_removed"))},
        "totals": totals, "groups": groups, "base_paths": _base_paths(manifest),
        "verification": {"at": verification["at"], "results": verification["results"]} if verification else None,
        "events": read_events(run_dir),
        "warnings": ["%s (%d): %s" % (w["code"], w["count"], w["message"]) for w in manifest["warnings"]],
        "manual_items": ["%s (%s): %s" % (m["path"], m["op"], m["reason"]) for m in manifest["manual_items"]],
        "reconcile": ["%s: %s" % (r["path"], r["detail"]) for r in manifest["reconcile"][:MAX_LISTED]],
        "svn_only": manifest["svn_only"][:MAX_LISTED], "notes": manifest["notes"],
    }


def write(run_dir):
    """Rebuild dashboard.html from the manifest. Returns its path."""
    run_dir = os.path.abspath(run_dir)
    manifest = read_json(os.path.join(run_dir, "manifest.json"))
    with open(os.path.join(ASSETS, "dashboard.template.html"), "r", encoding="utf-8") as handle:
        template = handle.read()
    path = os.path.join(run_dir, DASHBOARD)
    write_text(path, template.replace("__G2S_DATA__", embed(build(run_dir, manifest))))
    return path
