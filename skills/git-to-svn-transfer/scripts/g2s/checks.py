"""Project checks (build, test, smoke) and the readiness they earn.

A group is 'ready' only on evidence: every required check that applies to its
cumulative state ran and passed. Anything else has a name of its own, so that a
check which could not run is never mistaken for one that passed.
"""
import os
import shlex
import shutil
import subprocess

from .common import G2SError, check_relpath

KINDS = ("build", "test", "smoke", "lint", "other")
# A check exits with this code to say "a prerequisite is missing here", which is
# different from "the code is wrong". The automake convention for a skipped test.
EXIT_PREREQUISITE_MISSING = 77
EXIT_COMMAND_NOT_FOUND = 127

READY, FAILED, BLOCKED, UNVERIFIED = "ready", "failed", "blocked", "unverified"


def parse_checks(plan, group_keys, cli_required=(), cli_each=(), cli_final=()):
    """Normalise checks from the plan and the command line. Raises with every fault."""
    errors, checks, names = [], [], set()
    raw_checks = plan.get("checks", [])
    if not isinstance(raw_checks, list):
        raise G2SError("plan.checks must be a list")
    for position, raw in enumerate(raw_checks, start=1):
        label = "check %d" % position
        if not isinstance(raw, dict):
            errors.append("%s is not an object" % label)
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("%s: 'name' is required" % label)
        elif name in names:
            errors.append("%s: duplicate name '%s'" % (label, name))
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append("%s: 'command' is required" % label)
        else:
            try:
                if not shlex.split(command):
                    errors.append("%s: 'command' is empty" % label)
            except ValueError as exc:
                errors.append("%s: 'command' cannot be parsed (%s)" % (label, exc))
        kind = raw.get("kind", "other")
        if kind not in KINDS:
            errors.append("%s: 'kind' must be one of %s" % (label, ", ".join(KINDS)))
        where = raw.get("where", "each")
        if where not in ("each", "final"):
            errors.append("%s: 'where' must be 'each' or 'final'" % label)
        from_group = raw.get("from_group")
        if from_group is not None and from_group not in group_keys:
            errors.append("%s: 'from_group' names no group: %s" % (label, from_group))
        requires = raw.get("requires", [])
        if not isinstance(requires, list) or not all(isinstance(r, str) and r for r in requires):
            errors.append("%s: 'requires' must be a list of executable names" % label)
            requires = []
        cwd = raw.get("cwd")
        if cwd is not None:
            try:
                check_relpath(cwd)
            except G2SError as exc:
                errors.append("%s: 'cwd' %s" % (label, exc))
        timeout = raw.get("timeout_seconds")
        if timeout is not None and not (isinstance(timeout, int) and timeout > 0):
            errors.append("%s: 'timeout_seconds' must be a positive integer" % label)
        names.add(name)
        checks.append({"name": name, "kind": kind, "command": command, "where": where,
                       "required": bool(raw.get("required", True)), "requires": list(requires),
                       "from_group": from_group, "cwd": cwd, "timeout_seconds": timeout})
    if errors:
        raise G2SError("invalid checks in the plan:\n  - " + "\n  - ".join(errors))

    def from_cli(commands, prefix, where, required):
        for number, command in enumerate(commands, start=1):
            if not shlex.split(command):
                raise G2SError("empty check command")
            checks.append({"name": "%s-%d" % (prefix, number), "kind": "other", "command": command,
                           "where": where, "required": required, "requires": [], "from_group": None,
                           "cwd": None, "timeout_seconds": None})

    from_cli(cli_required, "required", "each", True)
    from_cli(cli_each, "optional-each", "each", False)
    from_cli(cli_final, "optional-final", "final", False)
    return checks


def applies(check, position, groups):
    """Does this check apply to the cumulative state after groups[position]?"""
    if check["where"] == "final" and position != len(groups) - 1:
        return False
    if check["from_group"]:
        start = [i for i, g in enumerate(groups) if g["key"] == check["from_group"]][0]
        return position >= start
    return True


def run_check(check, root, log_path, default_timeout=None):
    """Run one check under root. Returns {status, exit_code, reason}."""
    missing = [name for name in check["requires"] if shutil.which(name) is None]
    cwd = os.path.join(root, *check["cwd"].split("/")) if check["cwd"] else root
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "wb") as log:
        log.write(("$ %s\n(cwd: %s)\n\n" % (check["command"], cwd)).encode("utf-8"))
        if missing:
            reason = "required executable(s) not found: %s" % ", ".join(missing)
            log.write((reason + "\n").encode("utf-8"))
            return {"status": "blocked", "exit_code": None, "reason": reason}
        if not os.path.isdir(cwd):
            reason = "directory does not exist in this state: %s" % check["cwd"]
            log.write((reason + "\n").encode("utf-8"))
            return {"status": "failed", "exit_code": None, "reason": reason}
        log.flush()
        try:
            code = subprocess.run(shlex.split(check["command"]), cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=check["timeout_seconds"] or default_timeout).returncode
        except FileNotFoundError:
            return {"status": "blocked", "exit_code": EXIT_COMMAND_NOT_FOUND,
                    "reason": "command not found: %s" % shlex.split(check["command"])[0]}
        except subprocess.TimeoutExpired:
            return {"status": "timed_out", "exit_code": None, "reason": "timed out"}
    if code == 0:
        return {"status": "passed", "exit_code": 0, "reason": ""}
    if code == EXIT_COMMAND_NOT_FOUND:
        return {"status": "blocked", "exit_code": code, "reason": "command not found (exit 127)"}
    if code == EXIT_PREREQUISITE_MISSING:
        return {"status": "blocked", "exit_code": code,
                "reason": "the check reported a missing prerequisite (exit 77)"}
    return {"status": "failed", "exit_code": code, "reason": "exit %d" % code}


def run_for_state(checks, position, groups, root, log_dir, log_prefix, default_timeout=None):
    """Run every check that applies to one cumulative state."""
    results = []
    for number, check in enumerate(checks, start=1):
        if not applies(check, position, groups):
            continue
        log = os.path.join(log_dir, "%s-%02d-%s.log" % (log_prefix, number, _slug(check["name"])))
        outcome = run_check(check, root, log, default_timeout)
        results.append(dict(outcome, name=check["name"], kind=check["kind"], command=check["command"],
                            required=check["required"], group=groups[position]["id"], log=log))
    return results


def _slug(text):
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")[:40] or "check"


def readiness(results, required_expected):
    """Readiness of one state from its check results.

    required_expected: how many required checks apply to the state. With none, the
    state is unverified: nothing was asked of it, so nothing was shown.
    """
    required = [r for r in results if r["required"]]
    failed = [r for r in required if r["status"] == "failed"]
    blocked = [r for r in required if r["status"] == "blocked"]
    unfinished = [r for r in required if r["status"] in ("timed_out", "not_run")]
    if failed:
        return FAILED, ["required %s check '%s' failed (%s)" % (r["kind"], r["name"], r["reason"]) for r in failed]
    if blocked:
        return BLOCKED, ["required %s check '%s' could not run: %s" % (r["kind"], r["name"], r["reason"])
                         for r in blocked]
    if required_expected == 0:
        return UNVERIFIED, ["no required check applies to this state"]
    if unfinished or len(required) < required_expected:
        return UNVERIFIED, ["required check '%s' did not finish (%s)" % (r["name"], r["status"])
                            for r in unfinished] or ["required checks have not been run"]
    return READY, []
