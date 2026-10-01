"""Render GROUPS.md and SVN_MANUAL_TRANSFER.md from the templates in assets/."""
import os
import string

from .checks import applies as _applies
from .common import write_text

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets")


def _template(name):
    with open(os.path.join(ASSETS, name), "r", encoding="utf-8") as handle:
        return string.Template(handle.read())


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _counts(group):
    ops = [o for o in group["operations"] if not o["manual"]]
    return {
        "add": sum(1 for o in ops if o["op"] == "add" and not o["rename_from"]),
        "modify": sum(1 for o in ops if o["op"] == "modify"),
        "delete": sum(1 for o in ops if o["op"] == "delete" and not o["rename_to"]),
        "rename": sum(1 for o in ops if o["rename_from"]),
        "manual": sum(1 for o in group["operations"] if o["manual"]),
        "binary": sum(1 for o in ops if o["binary"]),
    }


def _revision(group):
    rev = group["svn_revision"]
    if rev["revision"] is None:
        return "", "not committed"
    return "r%d" % rev["revision"], rev["status"].replace("_", " ")


def _bullets(items, empty="None."):
    return "\n".join("- " + item for item in items) if items else empty


def _baseline(manifest):
    base = manifest.get("svn_baseline")
    if not base:
        return "not recorded"
    parts = [base.get("url") or "URL not given"]
    if base.get("revision") is not None:
        parts.append("r%s" % base["revision"])
    parts.append("(%s)" % base["source"])
    return " ".join(parts)


def _base_text(manifest):
    base = manifest["base"]
    if not base["sha"]:
        return "tree %s (synthesised baseline)" % base["tree"]
    return base["sha"] if base["ref"] in (None, base["sha"]) else "%s (%s)" % (base["sha"], base["ref"])


def _file_list(group, limit=40):
    labels = []
    for op in group["operations"]:
        if op["manual"]:
            label = "manual"
        elif op["rename_from"]:
            label = "renamed from `%s`" % op["rename_from"]
        elif op["rename_to"]:
            continue
        else:
            label = {"add": "added", "modify": "modified", "delete": "deleted"}[op["op"]]
        labels.append("- `%s` (%s%s)" % (op["path"], label, ", binary" if op["binary"] else ""))
    shown = labels[:limit]
    if len(labels) > limit:
        shown.append("- ... and %d more; the full lists are `add.txt`, `modify.txt`, `delete.txt` and "
                     "`rename.tsv` in the package" % (len(labels) - limit))
    return "\n".join(shown)


def _mode_text(manifest):
    return {
        "incremental": "incremental sync from Git base %s" % (manifest["base"]["sha"] or "")[:12],
        "initial-svn-wc": "first import; baseline derived from the SVN working copy",
        "initial-empty": "first import into an empty SVN location",
    }[manifest["mode"]]


def render_documents(run_dir, manifest):
    common = {
        "run_id": manifest["run_id"], "run_dir": run_dir, "mode": _mode_text(manifest),
        "repository": manifest["repository"],
        "base": _base_text(manifest),
        "target": "%s (%s)%s" % (manifest["target"]["sha"], manifest["target"]["ref"],
                                 ", plus uncommitted changes" if manifest["target"]["include_uncommitted"] else ""),
        "svn_baseline": _baseline(manifest), "branch": manifest["branch"],
        "include": ", ".join(manifest["scope"]["include"]) or "everything",
        "exclude": ", ".join(manifest["scope"]["exclude"]) or "nothing",
        "group_count": len(manifest["groups"]),
        "tool": os.path.normpath(os.path.join(ASSETS, "..", "scripts", "git_to_svn.py")),
    }
    client = manifest.get("svn_client") or {}
    baseline = manifest.get("svn_baseline") or {}
    common["svn_bin"] = client.get("binary") or "svn"
    common["svn_wc"] = baseline.get("working_copy") or "/path/to/dedicated/svn/working-copy"
    common["native_fn"] = ('native() { wslpath -w "$1"; }   # this svn client is a Windows program run from WSL'
                           if client.get("windows_client_under_posix") else
                           'native() { printf %s "$1"; }    # with a Windows svn.exe under WSL use: wslpath -w "$1"')
    common["baseline_revision"] = ("r%s" % baseline["revision"]) if baseline.get("revision") is not None \
        else "the baseline revision"
    ids = {g["key"]: g["id"] for g in manifest["groups"]}

    def depends(group):
        return ", ".join("%s (%s)" % (ids[k], k) for k in group["depends_on"]) or "none"

    rows, sections, mapping = [], [], []
    section = _template("group-section.template.md")
    for group in manifest["groups"]:
        c = _counts(group)
        rev, state = _revision(group)
        before = (group.get("svn_precommit") or {}).get("status") or "not run"
        rows.append("| %s | %s | `%s` | %d | %d | %d | %d | %d | %s | **%s** | %s | %s |" % (
            group["id"], _cell(group["title"]), (group["commit"] or "")[:10], c["add"], c["modify"],
            c["delete"], c["rename"], c["manual"], depends(group), group["readiness"]["status"], before,
            (rev + " " + state).strip()))
        mapping.append("| %s | `%s` | %s | %s |" % (group["id"], (group["commit"] or "")[:10], rev, state))
        ops = [o for o in group["operations"] if not o["manual"]]
        props = []
        if any(o["exec"] == "set" for o in ops):
            props.append("Set the executable property on the files marked `set` in `executable.txt`:\n\n"
                         "```bash\n\"$SVN\" propset svn:executable ON <path>\n```")
        if any(o["exec"] == "unset" for o in ops):
            props.append("Clear it on the files marked `unset`:\n\n```bash\n\"$SVN\" propdel svn:executable <path>\n```")
        manual = ["`%s` (%s): %s" % (o["path"], o["op"], o["manual"]) for o in group["operations"] if o["manual"]]
        sections.append(section.substitute(
            common, id=group["id"], title=group["title"], rationale=group["rationale"],
            commit=group["commit"] or "", worktree=os.path.join(run_dir, group["worktree"]),
            package=os.path.join(run_dir, group["package"]),
            depends=depends(group), files=_file_list(group),
            readiness=group["readiness"]["status"],
            readiness_reasons=_bullets(group["readiness"]["reasons"], "Every required check passed on the "
                                       "cumulative worktree." if group["readiness"]["status"] == "ready" else "None."),
            group_checks=_bullets(["`%s` (%s, %s)" % (c["command"], c["kind"],
                                                       "required" if c["required"] else "optional")
                                   for c in manifest["check_commands"]
                                   if _applies(c, manifest["groups"].index(group), manifest["groups"])],
                                  "No check applies to this state."),
            adds=c["add"], modifies=c["modify"], deletes=c["delete"], renames=c["rename"],
            binaries=c["binary"], message=group["message"].rstrip("\n"),
            properties="\n\n".join(props) or "No property changes in this group.",
            manual=_bullets(manual, "No manual items in this group.")))

    warnings = ["**%s** (%d): %s" % (w["code"], w["count"], w["message"]) for w in manifest["warnings"]]
    reconcile = ["`%s`: %s" % (r["path"], r["detail"]) for r in manifest["reconcile"]]
    svn_only = ["`%s`" % p for p in manifest["svn_only"][:200]]
    checks = ["`%s` (%s, %s) in %s%s" % (
        c["command"], c["kind"], "required" if c["required"] else "optional",
        "every cumulative worktree" if c["where"] == "each" else "the final worktree",
        " from group '%s' on" % c["from_group"] if c["from_group"] else "") for c in manifest["check_commands"]]
    check_step = ("`precommit` runs these in the working copy:\n\n" + _bullets(checks)
                  if checks else "No check command was recorded for this run, so `precommit` can only compare "
                  "content and reports `unverified`. Run whatever build or test the project has by hand.")
    not_ready = [g for g in manifest["groups"] if g["readiness"]["status"] != "ready"]
    readiness_note = ("Every group passed its required checks on its cumulative worktree." if not not_ready else
                      "**Not every group is ready.** " + "; ".join(
                          "group %s is %s" % (g["id"], g["readiness"]["status"]) for g in not_ready)
                      + ". A failed group is never applied: fix the plan. A blocked or unverified group is "
                      "applied only on your decision (`--allow-not-ready`).")
    superseded_note = ("**This run was superseded by `%s`. Do not transfer from it.**\n\n"
                       % manifest["superseded_by"]) if manifest.get("superseded_by") else ""
    shared = dict(common, rows="\n".join(rows), mapping="\n".join(mapping),
                  warnings=_bullets(warnings), notes=_bullets(manifest["notes"]),
                  reconcile=_bullets(reconcile), svn_only=_bullets(svn_only),
                  checks=_bullets(checks, "No project check command was supplied."), check_step=check_step,
                  readiness_note=readiness_note, superseded_note=superseded_note,
                  sections="\n".join(sections))
    write_text(os.path.join(run_dir, "GROUPS.md"), _template("GROUPS.template.md").substitute(shared))
    write_text(os.path.join(run_dir, "SVN_MANUAL_TRANSFER.md"),
               _template("SVN_MANUAL_TRANSFER.template.md").substitute(shared))
