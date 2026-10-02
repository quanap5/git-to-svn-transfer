#!/usr/bin/env python3
"""Prepare Git changes for a manual, group-by-group transfer into SVN.

Never runs 'svn commit' or 'git push'. Results are printed as JSON on stdout;
problems the user must resolve go to stderr with exit code 2. Exit code 1 means
a verification failed. Step-by-step progress goes to stderr (see --progress).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from g2s import TOOL_VERSION, dashboard, planning, progress, transfer  # noqa: E402
from g2s import checks as checks_mod  # noqa: E402
from g2s.common import G2SError, read_json  # noqa: E402


def _source_options(parser):
    parser.add_argument("--repo", default=".", help="Git repository (default: current directory)")
    base = parser.add_argument_group("baseline (exactly one is required)")
    base.add_argument("--base", help="incremental sync: Git ref whose state SVN already holds")
    base.add_argument("--initial-from-svn-wc", action="store_true",
                      help="first import: derive the baseline from the files in --svn-wc")
    base.add_argument("--initial-empty", action="store_true",
                      help="first import: SVN holds nothing inside the scope")
    parser.add_argument("--target", default="HEAD", help="Git ref to transfer up to (default: HEAD)")
    parser.add_argument("--svn-wc", help="SVN working copy used to check the baseline")
    parser.add_argument("--svn-url", help="SVN URL, recorded as stated when no working copy is given")
    parser.add_argument("--svn-revision", type=int, help="SVN baseline revision, recorded as stated")
    parser.add_argument("--svn-bin", help="svn executable (default: G2S_SVN, then PATH)")
    parser.add_argument("--groups", type=int, default=10,
                        help="desired number of groups; a hint for whoever writes the plan (default: 10)")
    parser.add_argument("--include", action="append", default=[], metavar="PATTERN",
                        help="limit the scope to matching paths (repeatable)")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                        help="leave matching paths out of the scope (repeatable)")
    parser.add_argument("--include-uncommitted", action="store_true",
                        help="also transfer uncommitted and untracked changes of the checkout (default: no)")
    parser.add_argument("--allow-flagged", action="append", default=[], metavar="PATTERN",
                        help="accept paths that look like secrets, dependencies or build output")


def build_parser():
    parser = argparse.ArgumentParser(prog="git_to_svn.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=TOOL_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="report the delta, the SVN state and special cases (read-only)")
    _source_options(p)
    p.add_argument("--json-out", help="write the report to this file instead of stdout")

    p = sub.add_parser("plan", help="validate a group plan against the delta (read-only)")
    _source_options(p)
    p.add_argument("--plan", required=True, help="group plan JSON")

    p = sub.add_parser("prepare", help="create the branch, commits, worktrees, packages and guides")
    _source_options(p)
    p.add_argument("--plan", required=True, help="group plan JSON")
    p.add_argument("--out", required=True, help="output directory outside the repository")
    p.add_argument("--required-check", action="append", default=[], metavar="COMMAND",
                   help="build, test or smoke command every cumulative worktree must pass (repeatable); "
                        "the plan's 'checks' list can say the same with names, kinds and prerequisites")
    p.add_argument("--check-each", action="append", default=[], metavar="COMMAND",
                   help="optional check run in every group worktree; reported, does not gate readiness")
    p.add_argument("--check", action="append", default=[], metavar="COMMAND",
                   help="optional check run in the final worktree only; reported, does not gate readiness")
    p.add_argument("--supersedes", metavar="RUN",
                   help="earlier run this one replaces, after its groups failed their checks")

    p = sub.add_parser("resume", help="report drift and finish an interrupted prepare")
    p.add_argument("--run", required=True)
    p.add_argument("--svn-wc")
    p.add_argument("--svn-bin")

    p = sub.add_parser("verify", help="check the chain, the final snapshot, packages and worktrees")
    p.add_argument("--run", required=True)
    p.add_argument("--svn-wc")
    p.add_argument("--svn-bin")
    p.add_argument("--svn-final", action="store_true",
                   help="also require the working copy to hold the final state")
    p.add_argument("--run-checks", action="store_true",
                   help="run the checks on every cumulative worktree and set each group's readiness")
    p.add_argument("--check-timeout", type=int, default=None, metavar="SECONDS")

    p = sub.add_parser("apply", help="stage one group in an SVN working copy; dry run unless --execute")
    p.add_argument("--run", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--svn-wc", required=True)
    p.add_argument("--svn-bin")
    p.add_argument("--execute", action="store_true", help="really copy and schedule; never commits")
    p.add_argument("--allow-unverified-previous", action="store_true")
    p.add_argument("--allow-not-ready", action="store_true",
                   help="apply a group that is blocked or unverified; a failed group is never applied")

    p = sub.add_parser("precommit", help="re-check a staged group inside the SVN working copy before committing")
    p.add_argument("--run", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--svn-wc", required=True)
    p.add_argument("--svn-bin")
    p.add_argument("--skip-checks", action="store_true",
                   help="compare content only; the result is 'unverified', never 'ready'")
    p.add_argument("--check-timeout", type=int, default=None, metavar="SECONDS")

    p = sub.add_parser("record", help="record the SVN revision the user committed for a group")
    p.add_argument("--run", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--svn-wc", help="working copy used to verify the revision; without it nothing is verified")
    p.add_argument("--svn-bin")
    p.add_argument("--replace", action="store_true", help="replace a revision recorded earlier")

    p = sub.add_parser("cleanup", help="list this run's worktrees and branch; remove them with --execute")
    p.add_argument("--run", required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--discard-untracked", action="store_true",
                   help="also remove worktrees whose only differences are untracked files, such as check output")
    p = sub.add_parser("report", help="rebuild dashboard.html, the readable view of a run (read-only for the run)")
    p.add_argument("--run", required=True)

    for command in sub.choices.values():
        command.add_argument("--progress", choices=("auto", "plain", "off"), default="auto",
                             help="progress lines on stderr: 'auto' colours them on a terminal and keeps them "
                                  "plain otherwise, 'plain' never colours, 'off' prints none (default: auto)")
    return parser


def _record(command, run_dir, code, summary, group=None):
    """Add the command to the run's history and rebuild its dashboard. Never fails the command."""
    if not run_dir or not os.path.isfile(os.path.join(run_dir, "manifest.json")):
        return None
    try:
        dashboard.log_event(run_dir, command, code, summary, group)
        return dashboard.write(run_dir)
    except (G2SError, OSError, KeyError, ValueError) as exc:
        sys.stderr.write("warning: dashboard.html was not updated: %s\n" % exc)
        return None


def _summary(command, result):
    """One line for the closing progress message."""
    if command == "verify":
        return ", ".join("%s %s" % pair for pair in sorted(result["readiness"].items()))
    if command in ("precommit", "record"):
        return "group %s %s" % (result["group"], result["status"])
    if command == "apply":
        return "group %s %s" % (result["group"], "staged, not committed" if result["executed"] else "dry run")
    if command == "prepare":
        return result["run"]
    if command in ("inspect", "plan"):
        return "ready to prepare" if result["ready_to_prepare"] else "%d blocker(s)" % len(result["blockers"])
    if command == "resume":
        return "state %s" % result["state"]
    if command == "report":
        return result["dashboard"]
    return "removed" if result["executed"] else "dry run"


def _plan_report(opts):
    scratch = tempfile.mkdtemp(prefix="g2s-plan-")
    try:
        analysis = transfer.analyse(opts, os.path.join(scratch, "index"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    groups, notes = planning.validate_plan(read_json(opts["plan"]), analysis["ops"], analysis["renames"])
    conflicts = transfer.tree_conflicts(analysis["git"], analysis["base"]["tree"], groups)
    if conflicts:
        raise G2SError("plan order is impossible:\n  - " + "\n  - ".join(conflicts[:20]))
    checks = checks_mod.parse_checks(read_json(opts["plan"]), [g["key"] for g in groups])
    required_per_group = [sum(1 for c in checks if c["required"] and checks_mod.applies(c, i, groups))
                          for i in range(len(groups))]
    return {
        "valid": True, "desired_groups": opts.get("groups") or 10, "group_count": len(groups),
        "checks": [{k: c[k] for k in ("name", "kind", "required", "where", "from_group", "requires")}
                   for c in checks],
        "groups_without_a_required_check": [g["id"] for g, n in zip(groups, required_per_group) if n == 0],
        "blockers": analysis["blockers"], "ready_to_prepare": not analysis["blockers"],
        "notes": notes,
        "groups": [{"id": g["id"], "key": g["key"], "title": g["title"], "depends_on": g["depends_on"],
                    "operations": len(g["ops"]),
                    "add": sum(1 for o in g["ops"] if o["op"] == "add"),
                    "modify": sum(1 for o in g["ops"] if o["op"] == "modify"),
                    "delete": sum(1 for o in g["ops"] if o["op"] == "delete"),
                    "manual": sum(1 for o in g["ops"] if o["manual"])} for g in groups],
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    opts = vars(args)
    code = 0
    progress.configure(args.progress)
    progress.start(args.command, opts.get("run") or opts.get("repo") or "")
    try:
        if args.command == "inspect":
            result = transfer.inspect(opts)
            if args.json_out:
                with open(args.json_out, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(result, handle, indent=1, ensure_ascii=False)
                result = {"written": os.path.abspath(args.json_out),
                          "read_the_file_for": "operations, commits, by_directory and the full lists below",
                          "counts": result["counts"], "blockers": result["blockers"],
                          "warnings": ["%s (%d)" % (w["code"], w["count"]) for w in result["warnings"]],
                          "notes": result["notes"],
                          "manual_items": len(result["manual_items"]), "reconcile": len(result["reconcile"]),
                          "svn_only": len(result["svn_only"]), "renames": len(result["renames"]),
                          "ready_to_prepare": result["ready_to_prepare"]}
        elif args.command == "plan":
            result = _plan_report(opts)
        elif args.command == "prepare":
            run_dir = transfer.prepare(opts)
            result = {"run": run_dir, "manifest": os.path.join(run_dir, "manifest.json"),
                      "groups_table": os.path.join(run_dir, "GROUPS.md"),
                      "guide": os.path.join(run_dir, "SVN_MANUAL_TRANSFER.md")}
        elif args.command == "resume":
            result = transfer.resume(args.run, args.svn_wc, args.svn_bin)
        elif args.command == "verify":
            result = transfer.verify(args.run, args.svn_wc, args.svn_bin, args.run_checks,
                                     args.svn_final, args.check_timeout)
            code = 1 if result["summary"]["failed"] else 0
        elif args.command == "apply":
            result = transfer.apply_group(args.run, args.group, args.svn_wc, args.svn_bin,
                                          args.execute, args.allow_unverified_previous, args.allow_not_ready)
            code = 1 if result.get("post_check", {}).get("status") == "failed" else 0
        elif args.command == "precommit":
            result = transfer.precommit(args.run, args.group, args.svn_wc, args.svn_bin,
                                        args.skip_checks, args.check_timeout)
            code = 1 if result["status"] == "failed" else 0
        elif args.command == "record":
            result = transfer.record_revision(args.run, args.group, args.revision, args.svn_wc,
                                              args.svn_bin, args.replace)
            code = 1 if result["status"] == "mismatch" else 0
        elif args.command == "report":
            if not os.path.isfile(os.path.join(args.run, "manifest.json")):
                raise G2SError("no manifest.json in %s; --run must name a run directory" % args.run)
            result = {"dashboard": dashboard.write(args.run)}
        else:
            result = transfer.cleanup(args.run, args.execute, args.discard_untracked)
    except G2SError as exc:
        progress.finish(2, "stopped")
        _record(args.command, opts.get("run"), 2, str(exc).splitlines()[0], opts.get("group"))
        sys.stderr.write("error: %s\n" % exc)
        return 2
    summary = _summary(args.command, result)
    if args.command != "report":
        written = _record(args.command, result["run"] if args.command == "prepare" else opts.get("run"), code,
                          "run prepared" if args.command == "prepare" else summary, opts.get("group"))
        if args.command == "prepare":
            result["dashboard"] = written
    progress.finish(code, summary)
    json.dump(result, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
