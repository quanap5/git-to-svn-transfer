"""Work out the next step of a run from its manifest, with the exact commands for it.

Read-only: it runs no git or svn command and changes nothing in the run. What the
manifest cannot know, such as whether files were already copied into the working
copy, is left to the commands it hands out ('svn status' first, always).

Every step says who runs it. 'you' steps change the SVN working copy or commit;
they belong to the user. 'agent' steps only read, or run this tool's own checks.
"""
import os
import shlex

from .render import ASSETS, native_function
from .transfer import _load

TOOL = os.path.normpath(os.path.join(ASSETS, "..", "scripts", "git_to_svn.py"))
WC_PLACEHOLDER = "/path/to/dedicated/svn/working-copy"
FINAL_CHECK = "SVN working copy holds the final state"


def _step(title, by, commands, expect=""):
    return {"title": title, "by": by, "commands": commands, "expect": expect}


def _files(group):
    ops = group["operations"]
    return {
        "add": [o["path"] for o in ops if not o["manual"] and o["op"] == "add" and not o["rename_from"]],
        "modify": [o["path"] for o in ops if not o["manual"] and o["op"] == "modify"],
        "delete": [o["path"] for o in ops if not o["manual"] and o["op"] == "delete" and not o["rename_to"]],
        "rename": [{"from": o["rename_from"], "to": o["path"]} for o in ops if not o["manual"] and o["rename_from"]],
        "executable": [{"change": o["exec"], "path": o["path"]} for o in ops if not o["manual"] and o["exec"]],
        "manual": [{"op": o["op"], "path": o["path"], "reason": o["manual"]} for o in ops if o["manual"]],
    }


def _group_view(group):
    precommit = group.get("svn_precommit") or {}
    return {
        "id": group["id"], "key": group["key"], "title": group["title"],
        "readiness": group["readiness"]["status"], "readiness_reasons": group["readiness"]["reasons"],
        "precommit": precommit.get("status"), "precommit_reasons": precommit.get("reasons") or [],
        "revision": group["svn_revision"]["revision"], "revision_status": group["svn_revision"]["status"],
        "message": group["message"],
    }


def _expected_revision(manifest, position):
    """The revision the working copy should be at before this group: the last one recorded, else the baseline."""
    for group in reversed(manifest["groups"][:position]):
        if group["svn_revision"]["revision"] is not None:
            return group["svn_revision"]["revision"]
    return (manifest.get("svn_baseline") or {}).get("revision")


def _stage_steps(manifest, position, files):
    """Steps 1 to 5 of the manual guide for one group, keeping only the parts this group needs."""
    expected = _expected_revision(manifest, position)
    pkg = '"$RUN/groups/$N'
    steps = [
        _step("Bring the working copy up to date", "you", ['cd "$WC"', '"$SVN" update']),
        _step("Confirm the working copy is clean and at the expected revision", "agent",
              ['"$SVN" status', '"$SVN" info --show-item revision'],
              "'svn status' prints nothing and the revision is %s. Anything else: stop, someone else committed "
              "or files were already copied; look before going on." % (
                  "r%s" % expected if expected is not None else "the one SVN held before this group")),
    ]
    if files["add"] or files["modify"] or files["rename"]:
        steps.append(_step("Copy this group's added and modified files into the working copy", "you",
                           ['cp -R %s/payload/." "$WC/"' % pkg],
                           "Copy only from payload/. On Windows Explorer: copy the contents of groups\\%s\\payload "
                           "into the working copy and choose Replace." % manifest["groups"][position]["id"]))
    if files["rename"]:
        steps.append(_step("Rename with svn move, so history follows the file", "you", [
            "while IFS=$'\\t' read -r old new; do",
            '  [ -n "$old" ] || continue',
            '  rm "$new"',
            '  "$SVN" move --parents -- "$old" "$new"',
            '  cp "$RUN/groups/$N/payload/$new" "$new"',
            'done < "$RUN/groups/$N/rename.tsv"'],
            "Never replace a rename with a delete and an add."))
    if files["add"]:
        steps.append(_step("Schedule the added files", "you",
                           ['"$SVN" add --parents --targets "$(native %s/svn-targets-add.txt")"' % pkg]))
    if files["delete"]:
        steps.append(_step("Schedule the deleted files", "you",
                           ['"$SVN" delete --targets "$(native %s/svn-targets-delete.txt")"' % pkg]))
    if files["executable"]:
        steps.append(_step("Set or clear the executable property", "you", [
            ('"$SVN" propset svn:executable ON %s' if e["change"] == "set" else '"$SVN" propdel svn:executable %s')
            % shlex.quote(e["path"]) for e in files["executable"]]))
    if files["manual"]:
        steps.append(_step("Handle the items the tool does not transfer", "you", [],
                           "; ".join("%s (%s): %s" % (m["path"], m["op"], m["reason"]) for m in files["manual"])))
    checks = ['"$SVN" status', '"$SVN" diff --summarize']
    if files["add"] or files["modify"] or files["rename"]:
        checks.append('( cd "$WC" && sha256sum -c "$RUN/groups/$N/sha256sums.txt" )')
    steps.append(_step("Look at what is staged", "agent", checks,
                       "Only A, M and D on this group's paths. '?' means a file was not added or something extra "
                       "slipped in; '!' means an added file is missing; 'C' or '~' means stop."))
    steps.append(_step("Check the working copy itself before committing", "agent",
                       ['python3 "$TOOL" precommit --run "$RUN" --group "$N" --svn-wc "$WC" --svn-bin "$SVN"'],
                       "Commit only on 'ready'. On 'failed' do not commit."))
    return steps


def _commit_steps():
    return [
        _step("Last look", "agent", ['"$SVN" status']),
        _step("Commit, yourself", "you", ['"$SVN" commit -F "$(native "$RUN/groups/$N/message.txt")"'],
              "svn prints 'Committed revision <REV>.' Keep that number."),
        _step("Record and verify the revision", "agent",
              ['"$SVN" update',
               'python3 "$TOOL" record --run "$RUN" --group "$N" --revision <REV> --svn-wc "$WC" --svn-bin "$SVN"'],
              "'verified' means the group is transferred. Do not start the next group on 'mismatch'."),
    ]


def _not_ready_decision(group):
    status = group["readiness"]["status"]
    if status == "ready":
        return None
    return ("Group %s is '%s' on its Git worktree (%s). Its checks could not prove that this state runs. Committing "
            "it anyway is the user's decision, never the agent's." % (
                group["id"], status, "; ".join(group["readiness"]["reasons"]) or "no reason recorded"))


def _final_checked(manifest):
    results = (manifest.get("verification") or {}).get("results") or []
    return any(r["check"] == FINAL_CHECK and r["status"] == "passed" for r in results)


def next_step(run_dir, svn_wc=None, svn_bin=None):
    run_dir = os.path.abspath(run_dir)
    manifest = _load(run_dir)
    client = manifest.get("svn_client") or {}
    wc = os.path.abspath(svn_wc) if svn_wc else (manifest.get("svn_baseline") or {}).get("working_copy")
    groups = manifest["groups"]
    verified = [g["id"] for g in groups if g["svn_revision"]["status"] == "verified"]
    position = next((i for i, g in enumerate(groups) if g["svn_revision"]["status"] != "verified"), None)
    group = groups[position] if position is not None else None

    result = {
        "run": run_dir, "run_id": manifest["run_id"],
        "progress": {"groups": len(groups), "verified": verified, "next_group": group["id"] if group else None},
        "stage": None, "why": "", "decision": None, "group": None, "files": None,
        "missing": [] if wc else ["the SVN working copy is not known; pass --svn-wc"],
        "setup": ["SVN=%s" % shlex.quote(svn_bin or client.get("binary") or "svn"),
                  "RUN=%s" % shlex.quote(run_dir),
                  "WC=%s" % shlex.quote(wc or WC_PLACEHOLDER),
                  "TOOL=%s" % shlex.quote(TOOL),
                  native_function(client)],
        "steps": [],
        "guide": os.path.join(run_dir, "SVN_MANUAL_TRANSFER.md"),
        "dashboard": os.path.join(run_dir, "dashboard.html"),
    }
    if group:
        result["setup"].append("N=%s" % group["id"])
        result["group"] = _group_view(group)

    def done(stage, why, steps=(), decision=None, files=None):
        result.update(stage=stage, why=why, steps=list(steps), decision=decision, files=files)
        return result

    if manifest["state"] != "prepared":
        return done("resume", "The run was not fully prepared (state '%s')." % manifest["state"],
                    [_step("Finish preparing the run", "agent", ['python3 "$TOOL" resume --run "$RUN"'])])
    if manifest.get("superseded_by"):
        return done("superseded", "This run was replaced by %s after a group failed its checks. Nothing from it "
                    "may go to SVN." % manifest["superseded_by"],
                    [_step("Continue with the newer run", "agent",
                           ['python3 "$TOOL" next --run %s' % shlex.quote(manifest["superseded_by"])])])
    failed = [g for g in groups if g["readiness"]["status"] == "failed"]
    if failed and group:
        return done("fix_plan", "Group(s) %s failed a required check on their cumulative Git worktree, so the plan "
                    "is wrong. A failed group never goes to SVN; %d of %d group(s) are already transferred." % (
                        ", ".join(g["id"] for g in failed), len(verified), len(groups)),
                    [_step("Read why", "agent", ['ls "$RUN/checks/"group-%s-*.log' % g["id"] for g in failed],
                           "; ".join("%s: %s" % (g["id"], "; ".join(g["readiness"]["reasons"])) for g in failed)),
                     _step("Regroup with the git-to-svn-transfer skill ('When a group fails'), prepare a new run "
                           "with --supersedes, and verify it", "agent", [])],
                    decision="Changing the plan is a decision the git-to-svn-transfer skill makes with the user.")
    if manifest.get("verification") is None:
        return done("verify", "The project checks have never been run on the cumulative worktrees, so no group "
                    "has earned 'ready' yet.",
                    [_step("Run the checks on every cumulative worktree", "agent",
                           ['python3 "$TOOL" verify --run "$RUN" --run-checks'])])

    if group is None:
        if not _final_checked(manifest):
            return done("final_verify", "Every group has a verified revision. What is left is proving that the "
                        "working copy holds the whole target.",
                        [_step("Verify the final state", "agent",
                               ['"$SVN" update',
                                'python3 "$TOOL" verify --run "$RUN" --svn-wc "$WC" --svn-bin "$SVN" --svn-final'])])
        cleaned = (manifest.get("cleanup") or {}).get("worktrees_removed")
        if not cleaned:
            return done("cleanup", "The transfer is complete and verified. The run's worktrees and the temporary "
                        "branch %s are still in the Git repository." % manifest["branch"],
                        [_step("List what would be removed", "agent", ['python3 "$TOOL" cleanup --run "$RUN"']),
                         _step("Remove it, only if you want to", "you",
                               ['python3 "$TOOL" cleanup --run "$RUN" --execute'],
                               "The manifest, the guides and the packages stay.")])
        return done("done", "Every group is in SVN and verified, the final state is checked and the run is "
                    "cleaned up. Nothing is left to do.")

    files = _files(group)
    revision = group["svn_revision"]
    if revision["status"] == "mismatch":
        return done("resolve_mismatch", "r%s was recorded for group %s but does not match it: %s" % (
                        revision["revision"], group["id"], "; ".join(revision["details"]) or "no detail recorded"),
                    [_step("See what the revision changed", "agent", ['"$SVN" log -v -r %s' % revision["revision"]]),
                     _step("Correct SVN with a follow-up commit, then record that revision instead", "you",
                           ['python3 "$TOOL" record --run "$RUN" --group "$N" --revision <REV> --svn-wc "$WC" '
                            '--svn-bin "$SVN" --replace'])],
                    decision="How to correct a revision that is already in SVN is the user's decision.",
                    files=files)
    if revision["status"] == "user_provided":
        return done("verify_record", "r%s was recorded for group %s without a working copy, so nothing was "
                    "compared. The group does not count as transferred yet." % (revision["revision"], group["id"]),
                    [_step("Record it again against the working copy", "agent",
                           ['"$SVN" update',
                            'python3 "$TOOL" record --run "$RUN" --group "$N" --revision %s --svn-wc "$WC" '
                            '--svn-bin "$SVN"' % revision["revision"]])], files=files)

    decision = _not_ready_decision(group)
    precommit = (group.get("svn_precommit") or {}).get("status")
    if precommit is None:
        return done("stage", "Group %s (%s) is next: copy it into the working copy, schedule its changes and "
                    "check it there." % (group["id"], group["title"]),
                    _stage_steps(manifest, position, files), decision, files)
    if precommit == "failed":
        return done("fix_precommit", "The last check of group %s inside the working copy failed: %s. Do not "
                    "commit." % (group["id"], "; ".join(group["svn_precommit"]["reasons"]) or "no reason recorded"),
                    [_step("Undo the staging", "you", ['cd "$WC"', '"$SVN" revert -R .', '"$SVN" status'],
                           "Delete any unversioned file the copy or the checks left behind, then stage again.")]
                    + _stage_steps(manifest, position, files)[1:], decision, files)
    return done("commit", "Group %s is staged and its check in the working copy says '%s'. Commit it, then "
                "record the revision." % (group["id"], precommit),
                _commit_steps(), decision if precommit == "ready" else (
                    "The check in the working copy says '%s', not 'ready'. Committing anyway is the user's "
                    "decision." % precommit), files)
