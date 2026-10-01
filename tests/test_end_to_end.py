"""End-to-end tests against a throwaway Git repository and a local SVN repository.

Nothing here contacts a real SVN server: the repository is created with svnadmin
and reached through a file:// URL.

Environment:
  G2S_SVN, G2S_SVNADMIN  executables (default: 'svn' and 'svnadmin' on PATH)
  G2S_TEST_TMP           directory for temporary data. Required when the SVN client
                         is a Windows executable driven from WSL, because it must
                         live on a drive Windows can open.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

from g2s import transfer  # noqa: E402
from g2s.common import G2SError, read_json, sha256_file  # noqa: E402
from g2s.svnops import Svn  # noqa: E402

SVN_BIN = os.environ.get("G2S_SVN") or shutil.which("svn") or shutil.which("svn.exe")
SVNADMIN_BIN = os.environ.get("G2S_SVNADMIN") or shutil.which("svnadmin") or shutil.which("svnadmin.exe")
WINDOWS_CLIENT = bool(SVN_BIN) and SVN_BIN.lower().endswith(".exe") and os.name == "posix"
TMP_ROOT = os.environ.get("G2S_TEST_TMP")

SKIP = None
if not SVN_BIN or not SVNADMIN_BIN:
    SKIP = "svn and svnadmin are required (set G2S_SVN and G2S_SVNADMIN)"
elif WINDOWS_CLIENT and not TMP_ROOT:
    SKIP = "a Windows svn client needs G2S_TEST_TMP on a Windows-visible drive"

BINARY = bytes(range(256)) * 4 + b"\r\n\0\r\n"
UNICODE_DIR = "tài liệu"
UNICODE_FILE = UNICODE_DIR + "/ghi chú mới.md"


def sh(argv, cwd, check=True):
    proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise AssertionError("%s failed: %s" % (argv, proc.stderr.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8", "replace")


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


@unittest.skipIf(SKIP, SKIP)
class TransferTestCase(unittest.TestCase):
    def setUp(self):
        if TMP_ROOT:
            os.makedirs(TMP_ROOT, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix="g2s-test-", dir=TMP_ROOT)
        self.addCleanup(self._cleanup)
        self.repo = os.path.join(self.tmp, "git-repo")
        self.out = os.path.join(self.tmp, "out")
        self.wc = os.path.join(self.tmp, "svn-wc")
        self.svn = Svn(SVN_BIN)
        os.makedirs(self.repo)
        sh(["git", "init", "-q", "-b", "main"], self.repo)
        for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            sh(["git", "config", key, value], self.repo)

    def _cleanup(self):
        shutil.rmtree(self.tmp, onerror=_force_remove)

    # --- helpers
    def write(self, root, path, data):
        full = os.path.join(root, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))

    def commit(self, message):
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", message], self.repo)
        return sh(["git", "rev-parse", "HEAD"], self.repo).strip()

    def git(self, *args):
        return sh(["git"] + list(args), self.repo).strip()

    def svn_run(self, *args, **kw):
        return sh([SVN_BIN, "--non-interactive"] + list(args), kw.get("cwd", self.wc))

    def make_svn(self, seed_from_repo=True):
        """Create a local SVN repository and a working copy; optionally seed it from the Git checkout."""
        store = os.path.join(self.tmp, "svn-store")
        sh([SVNADMIN_BIN, "create", self.svn.native(store)], self.tmp)
        if WINDOWS_CLIENT:
            url = "file:///" + sh(["wslpath", "-m", store], self.tmp).strip()
        else:
            url = "file://" + store
        sh([SVN_BIN, "--non-interactive", "mkdir", "-q", "-m", "trunk", url + "/trunk"], self.tmp)
        sh([SVN_BIN, "--non-interactive", "checkout", "-q", url + "/trunk", self.svn.native(self.wc)], self.tmp)
        if seed_from_repo:
            for path in self.git("ls-files", "-z").split("\0"):
                if path:
                    with open(os.path.join(self.repo, *path.split("/")), "rb") as handle:
                        self.write(self.wc, path, handle.read())
            self.svn_run("add", "-q", "--force", ".")
            self.svn_commit("seed")
        return url

    def svn_commit(self, message):
        self.svn_run("commit", "-q", "-m", message)
        self.svn_run("update", "-q")
        return self.svn.info(self.wc)["revision"]

    def plan(self, groups):
        path = os.path.join(self.tmp, "plan.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "groups": groups}, handle)
        return path

    def opts(self, **kw):
        base = {"repo": self.repo, "target": "HEAD", "out": self.out, "svn_bin": SVN_BIN,
                "include": [], "exclude": [], "allow_flagged": [], "check": [], "check_each": [],
                "required_check": []}
        base.update(kw)
        return base

    def group(self, key, paths, depends_on=()):
        return {"key": key, "title": key.title(), "rationale": "Belongs together.",
                "message": "feat(%s): transfer %s\n\n- generated by the test" % (key, key),
                "paths": list(paths), "depends_on": list(depends_on)}

    def source_state(self):
        return (self.git("rev-parse", "HEAD"), self.git("symbolic-ref", "HEAD"),
                self.git("status", "--porcelain"), self.git("write-tree"))

    def base_repo(self):
        self.write(self.repo, "src/a.txt", "alpha\n")
        self.write(self.repo, "src/old_name.txt", "".join("line %d\n" % i for i in range(40)))
        self.write(self.repo, "docs/readme.md", "# readme\n")
        self.write(self.repo, "assets/data.bin", BINARY)
        self.write(self.repo, "to_delete.txt", "going away\n")
        self.write(self.repo, UNICODE_DIR + "/cũ.txt", "nội dung cũ\n")
        return self.commit("base")

    def target_repo(self):
        self.write(self.repo, "src/a.txt", "alpha\nbeta\n")
        self.git("mv", "src/old_name.txt", "src/new_name.txt")
        self.write(self.repo, "src/new_name.txt", "".join("line %d\n" % i for i in range(40)) + "extra\n")
        os.remove(os.path.join(self.repo, "to_delete.txt"))
        self.write(self.repo, "assets/data.bin", BINARY[::-1])
        self.write(self.repo, "assets/img.bin", BINARY * 3)
        self.write(self.repo, "assets/icons/logo@2x.png", b"\x89PNG\r\n\x1a\n\0\0")
        self.write(self.repo, UNICODE_FILE, "ghi chú\n")
        self.write(self.repo, "docs/name with space.txt", "spaces\n")
        self.write(self.repo, "run.sh", "#!/bin/sh\necho ok\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "update-index", "--chmod=+x", "run.sh"], self.repo)
        sh(["git", "commit", "-q", "-m", "target"], self.repo)
        return self.git("rev-parse", "HEAD")

    def three_groups(self):
        return self.plan([
            self.group("core", ["src/"]),
            self.group("assets", ["assets/"], ["core"]),
            self.group("docs", ["docs/", UNICODE_DIR + "/", "to_delete.txt", "run.sh"], ["core"]),
        ])

    def target_files(self, rev="HEAD"):
        files = {}
        for path in self.git("ls-tree", "-r", "-z", "--name-only", rev).split("\0"):
            if path:
                files[path] = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (rev, path)],
                                             cwd=self.repo, stdout=subprocess.PIPE, check=True).stdout
        return files


class IncrementalTransfer(TransferTestCase):
    def test_groups_accumulate_and_the_working_copy_ends_at_the_target(self):
        base = self.base_repo()
        self.make_svn()
        target = self.target_repo()
        before = self.source_state()

        report = transfer.inspect(self.opts(base=base, svn_wc=self.wc))
        self.assertTrue(report["ready_to_prepare"], report["blockers"])
        self.assertEqual(report["counts"], {"add": 6, "modify": 2, "delete": 2})

        run_dir = transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.three_groups(),
                                             required_check=[
                                                 "python3 -B -c \"import os,sys;sys.exit(0 if os.path.isfile('src/a.txt') else 1)\""]))
        self.assertEqual(self.source_state(), before, "the source repository must be untouched")
        self.assertFalse(run_dir.startswith(self.repo + os.sep))
        manifest = read_json(os.path.join(run_dir, "manifest.json"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual((manifest["base"]["sha"], manifest["target"]["sha"]), (base, target))
        self.assertEqual(manifest["state"], "prepared")
        for name in ("GROUPS.md", "SVN_MANUAL_TRANSFER.md"):
            self.assertTrue(os.path.isfile(os.path.join(run_dir, name)))

        # Worktrees are real, cumulative, and free of reports.
        listed = sh(["git", "worktree", "list", "--porcelain"], self.repo)
        for group in manifest["groups"]:
            worktree = os.path.join(run_dir, group["worktree"])
            self.assertIn(os.path.realpath(worktree), listed.replace("worktree ", ""))
            self.assertEqual(sh(["git", "rev-parse", "HEAD"], worktree).strip(), group["commit"])
        first, last = (os.path.join(run_dir, g["worktree"]) for g in (manifest["groups"][0], manifest["groups"][-1]))
        self.assertTrue(os.path.isfile(os.path.join(first, "src", "new_name.txt")))
        self.assertTrue(os.path.isfile(os.path.join(first, "to_delete.txt")), "group 01 has not deleted it yet")
        self.assertFalse(os.path.exists(os.path.join(last, "to_delete.txt")))
        self.assertEqual(sh(["git", "rev-parse", "HEAD^{tree}"], last).strip(), self.git("rev-parse", "HEAD^{tree}"))

        # The rename stays a rename because both halves are in one group.
        core = manifest["groups"][0]
        self.assertEqual(open(os.path.join(run_dir, core["package"], "rename.tsv")).read(),
                         "src/old_name.txt\tsrc/new_name.txt\n")
        # Binary payload is byte-identical.
        self.assertEqual(open(os.path.join(run_dir, "groups", "02", "payload", "assets", "img.bin"), "rb").read(),
                         BINARY * 3)

        verification = transfer.verify(run_dir, svn_wc=self.wc, svn_bin=SVN_BIN, run_checks=True)
        self.assertEqual(verification["summary"]["failed"], 0, verification["results"])
        statuses = {r["check"]: r["status"] for r in verification["results"]}
        self.assertEqual(statuses["final snapshot equals the whole target tree"], "passed")
        self.assertEqual(statuses["every group has a verified SVN revision"], "not_run")
        self.assertEqual(statuses["group 03 readiness: ready"], "passed")
        self.assertTrue(verification["run_ready"])

        # Dry run changes nothing.
        dry = transfer.apply_group(run_dir, "01", self.wc, SVN_BIN)
        self.assertFalse(dry["executed"])
        self.assertIn("svn move src/old_name.txt -> src/new_name.txt", dry["actions"])
        self.assertEqual(self.svn.snapshot(self.wc)["dirty"], [])
        self.assertFalse(os.path.exists(os.path.join(self.wc, "src", "new_name.txt")))

        # A later group is refused until the earlier one is verified.
        with self.assertRaises(G2SError) as ctx:
            transfer.apply_group(run_dir, "02", self.wc, SVN_BIN)
        self.assertIn("no verified SVN revision", str(ctx.exception))

        revisions = []
        for group in manifest["groups"]:
            applied = transfer.apply_group(run_dir, group["id"], self.wc, SVN_BIN, execute=True)
            self.assertEqual(applied["post_check"]["status"], "passed", applied["post_check"])
            staged = transfer.precommit(run_dir, group["id"], self.wc, SVN_BIN)
            self.assertEqual((staged["status"], staged["identical_to_git_worktree"]), ("ready", True), staged)
            revision = self.svn_commit(group["message"])
            revisions.append(revision)
            if group["id"] == "01":
                stated = transfer.record_revision(run_dir, "01", revision)
                self.assertEqual(stated["status"], "user_provided")
                with self.assertRaises(G2SError):
                    transfer.apply_group(run_dir, "02", self.wc, SVN_BIN)
            checked = transfer.record_revision(run_dir, group["id"], revision, self.wc, SVN_BIN)
            self.assertEqual(checked["status"], "verified", checked["details"])

        # A revision that belongs to another group is not accepted as this group's.
        with self.assertRaises(G2SError):
            transfer.record_revision(run_dir, "03", revisions[0], self.wc, SVN_BIN, replace=True)

        final = transfer.verify(run_dir, svn_wc=self.wc, svn_bin=SVN_BIN, svn_final=True)
        self.assertEqual(final["summary"]["failed"], 0, final["results"])
        statuses = {r["check"]: r["status"] for r in final["results"]}
        self.assertEqual(statuses["SVN working copy holds the final state"], "passed")
        self.assertEqual(statuses["every group has a verified SVN revision"], "passed")

        # The working copy is the target, file for file, including binary and Unicode names.
        for path, data in self.target_files().items():
            with open(os.path.join(self.wc, *path.split("/")), "rb") as handle:
                self.assertEqual(handle.read(), data, path)
        self.assertFalse(os.path.exists(os.path.join(self.wc, "to_delete.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.wc, "src", "old_name.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.wc, ".git")))
        self.assertEqual(self.svn.snapshot(self.wc)["dirty"], [])

        # SVN recorded the rename as a copy, and the executable bit as a property.
        log = self.svn.changed_paths(self.wc, revisions[0])
        moved = [c for c in log["changes"] if c["path"] == "src/new_name.txt"][0]
        self.assertTrue(moved["copyfrom"].endswith("src/old_name.txt"))
        self.assertIn("svn:executable", self.svn.properties(self.wc).get("run.sh", {}))
        self.assertEqual(self.source_state(), before)

    def test_command_line_reports_json_and_exit_codes(self):
        base = self.base_repo()
        self.target_repo()
        script = os.path.join(SCRIPTS, "git_to_svn.py")
        ok = subprocess.run([sys.executable, script, "inspect", "--repo", self.repo, "--base", base],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout.decode("utf-8"))["counts"]["add"], 6)
        undecided = subprocess.run([sys.executable, script, "inspect", "--repo", self.repo],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(undecided.returncode, 2)
        self.assertIn(b"baseline is not defined", undecided.stderr)
        inside = subprocess.run([sys.executable, script, "prepare", "--repo", self.repo, "--base", base,
                                 "--plan", self.three_groups(), "--out", os.path.join(self.repo, "x")],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(inside.returncode, 2)
        self.assertIn(b"outside the source repository", inside.stderr)


    def test_names_the_client_cannot_receive_are_reported_not_approximated(self):
        old = UNICODE_DIR + "/cũ.txt"
        base = self.base_repo()
        self.make_svn()
        os.remove(os.path.join(self.repo, *old.split("/")))
        self.write(self.repo, UNICODE_FILE, "ghi chú\n")
        self.commit("unicode changes")
        run_dir = transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.plan([self.group("all", ["**"])])))

        applied = transfer.apply_group(run_dir, "01", self.wc, SVN_BIN, execute=True, allow_not_ready=True)
        self.assertEqual(applied["post_check"]["status"], "passed", applied["post_check"])
        status = self.svn.status(self.wc)
        self.assertEqual(status[UNICODE_FILE]["item"], "added", "a Unicode-named file is added by its real name")
        if self.svn.windows_client:
            # The delete cannot be expressed to this client: it is left undone and named.
            self.assertEqual([m["path"] for m in applied["needs_manual_svn_step"]], [old])
            self.assertEqual(status[old]["item"], "normal")
            revision = self.svn_commit("partial")
            partial = transfer.record_revision(run_dir, "01", revision, self.wc, SVN_BIN)
            self.assertEqual(partial["status"], "mismatch", "an incomplete commit must not count as transferred")
            self.assertTrue(any("does not touch it" in d for d in partial["details"]))
        else:
            self.assertEqual(applied["needs_manual_svn_step"], [])
            self.assertEqual(status[old]["item"], "deleted")

    def test_full_command_line_flow_and_rendered_guide(self):
        base = self.base_repo()
        self.target_repo()
        script = os.path.join(SCRIPTS, "git_to_svn.py")

        def cli(*args):
            proc = subprocess.run([sys.executable, script] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return proc.returncode, (json.loads(proc.stdout.decode("utf-8")) if proc.stdout.strip() else None), proc.stderr

        source = ["--repo", self.repo, "--base", base, "--plan", self.three_groups()]
        code, planned, err = cli("plan", *source)
        self.assertEqual((code, [g["operations"] for g in planned["groups"]]), (0, [3, 3, 4]), err)
        code, prepared, err = cli("prepare", *(source + ["--out", self.out, "--check", "git status --short"]))
        self.assertEqual(code, 0, err)
        run_dir = prepared["run"]
        code, verified, _ = cli("verify", "--run", run_dir, "--run-checks")
        self.assertEqual((code, verified["summary"]["failed"]), (0, 0), verified)
        code, recorded, _ = cli("record", "--run", run_dir, "--group", "01", "--revision", "12")
        self.assertEqual((code, recorded["status"]), (0, "user_provided"))
        code, listed, _ = cli("cleanup", "--run", run_dir)
        self.assertEqual((code, listed["executed"], len(listed["would_remove"])), (0, False, 4))
        code, removed, _ = cli("cleanup", "--run", run_dir, "--execute")
        self.assertEqual((code, len(removed["removed"])), (0, 4))
        code, after, _ = cli("verify", "--run", run_dir)
        self.assertEqual(code, 0, after)

        with open(os.path.join(run_dir, "SVN_MANUAL_TRANSFER.md"), encoding="utf-8") as handle:
            guide = handle.read()
        with open(os.path.join(run_dir, "GROUPS.md"), encoding="utf-8") as handle:
            table = handle.read()
        for text in (guide, table):
            self.assertNotIn("$$", text)
            for placeholder in ("$run_id", "$run_dir", "$rows", "$sections", "$mapping", "$warnings",
                                "$checks", "$tool", "$branch", "$svn_baseline", "$reconcile", "$notes",
                                "$readiness", "$check_step", "$group_checks", "$superseded_note", "$files"):
                self.assertNotIn(placeholder, text)
        self.assertIn('precommit --run "$RUN" --group "$N"', guide)
        self.assertIn("**Not every group is ready.**", guide)
        for expected in ('"$SVN" update', 'N=01', '"$SVN" commit -F', "### Group 03: Docs", "| 01 | `", "| r12 | user provided |"):
            self.assertIn(expected, guide)
        self.assertIn("| 02 | Assets |", table)


class BaselineAndWorkingCopyGuards(TransferTestCase):
    def test_baseline_mismatch_stops_prepare_and_creates_nothing(self):
        base = self.base_repo()
        self.make_svn()
        self.write(self.wc, "src/a.txt", "changed in svn only\n")
        self.svn_commit("svn-side change")
        self.target_repo()

        report = transfer.inspect(self.opts(base=base, svn_wc=self.wc))
        self.assertFalse(report["ready_to_prepare"])
        self.assertEqual([m["path"] for m in report["baseline_mismatch"]], ["src/a.txt"])
        with self.assertRaises(G2SError) as ctx:
            transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.three_groups()))
        self.assertIn("does not match the baseline", str(ctx.exception))
        self.assertEqual([d for d in os.listdir(self.out) if os.path.isdir(os.path.join(self.out, d))], [])
        self.assertEqual(self.git("branch", "--list", "svn-transfer/*"), "")
        self.assertEqual(len(sh(["git", "worktree", "list"], self.repo).splitlines()), 1)

    def test_dirty_working_copy_blocks_inspect_and_apply(self):
        base = self.base_repo()
        self.make_svn()
        self.target_repo()
        run_dir = transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.three_groups()))

        self.write(self.wc, "docs/readme.md", "# local edit\n")
        report = transfer.inspect(self.opts(base=base, svn_wc=self.wc))
        self.assertTrue(any("uncommitted changes" in b for b in report["blockers"]))
        with self.assertRaises(G2SError) as ctx:
            transfer.apply_group(run_dir, "01", self.wc, SVN_BIN, execute=True)
        self.assertIn("uncommitted changes", str(ctx.exception))
        with open(os.path.join(self.wc, "docs", "readme.md")) as handle:
            self.assertEqual(handle.read(), "# local edit\n", "the local edit must survive")
        with open(os.path.join(self.wc, "src", "a.txt")) as handle:
            self.assertEqual(handle.read(), "alpha\n", "nothing may be copied when apply refuses")

    def test_svn_change_made_after_prepare_is_not_overwritten(self):
        base = self.base_repo()
        self.make_svn()
        self.target_repo()
        run_dir = transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.three_groups()))
        self.write(self.wc, "src/a.txt", "someone else committed this\n")
        self.svn_commit("concurrent change")
        with self.assertRaises(G2SError) as ctx:
            transfer.apply_group(run_dir, "01", self.wc, SVN_BIN, execute=True)
        self.assertIn("differs from the Git base", str(ctx.exception))
        with open(os.path.join(self.wc, "src", "a.txt")) as handle:
            self.assertEqual(handle.read(), "someone else committed this\n")

    def test_first_import_keeps_svn_only_files_and_reports_differences(self):
        self.write(self.repo, "shared/same.txt", "same\n")
        self.write(self.repo, "shared/differs.txt", "git version\n")
        self.write(self.repo, "shared/eol.txt", "one\ntwo\n")
        self.write(self.repo, "new/only-in-git.txt", "new\n")
        self.commit("everything")
        self.make_svn(seed_from_repo=False)
        self.write(self.wc, "shared/same.txt", "same\n")
        self.write(self.wc, "shared/differs.txt", "svn version\n")
        self.write(self.wc, "shared/eol.txt", "one\r\ntwo\r\n")
        self.write(self.wc, "svn-only/keep.bin", BINARY)
        self.svn_run("add", "-q", "--force", ".")
        self.svn_commit("pre-existing svn content")

        opts = self.opts(initial_from_svn_wc=True, svn_wc=self.wc,
                         plan=self.plan([self.group("all", ["**"])]))
        report = transfer.inspect(opts)
        self.assertEqual([o["path"] for o in report["operations"]], ["new/only-in-git.txt"])
        self.assertEqual({r["path"]: r["difference"] for r in report["reconcile"]},
                         {"shared/differs.txt": "content", "shared/eol.txt": "line-endings"})
        self.assertEqual(report["svn_only"], ["svn-only/keep.bin"])

        run_dir = transfer.prepare(opts)
        self.assertEqual(transfer.verify(run_dir, self.wc, SVN_BIN)["summary"]["failed"], 0)
        transfer.apply_group(run_dir, "01", self.wc, SVN_BIN, execute=True, allow_not_ready=True)
        revision = self.svn_commit("import")
        self.assertEqual(transfer.record_revision(run_dir, "01", revision, self.wc, SVN_BIN)["status"], "verified")
        with open(os.path.join(self.wc, "shared", "differs.txt")) as handle:
            self.assertEqual(handle.read(), "svn version\n", "an SVN-side difference is never overwritten")
        with open(os.path.join(self.wc, "svn-only", "keep.bin"), "rb") as handle:
            self.assertEqual(handle.read(), BINARY)
        with self.assertRaises(G2SError):
            transfer.inspect(self.opts(svn_wc=self.wc))


class ResumeAndIdempotence(TransferTestCase):
    def test_interrupted_prepare_resumes_without_duplicates(self):
        base = self.base_repo()
        self.target_repo()
        opts = self.opts(base=base, plan=self.three_groups())
        run_dir = transfer.prepare(opts, stop_after="worktree:01")
        self.assertEqual(read_json(os.path.join(run_dir, "manifest.json"))["state"], "planned")
        commits = [g["commit"] for g in read_json(os.path.join(run_dir, "manifest.json"))["groups"]]

        with self.assertRaises(G2SError) as ctx:
            transfer.prepare(opts)
        self.assertIn("already prepared", str(ctx.exception))
        self.assertEqual(len([d for d in os.listdir(self.out) if os.path.isdir(os.path.join(self.out, d))]), 1)

        resumed = transfer.resume(run_dir)
        self.assertEqual((resumed["state_before"], resumed["state"]), ("planned", "prepared"))
        manifest = read_json(os.path.join(run_dir, "manifest.json"))
        self.assertEqual([g["commit"] for g in manifest["groups"]], commits, "commits must not be recreated")
        self.assertEqual(len(sh(["git", "worktree", "list"], self.repo).splitlines()), 4)
        self.assertEqual(len(self.git("branch", "--list", "svn-transfer/*").splitlines()), 1)
        self.assertEqual(transfer.verify(run_dir)["summary"]["failed"], 0)

        again = transfer.resume(run_dir)
        self.assertEqual(again["actions"], [])
        self.assertEqual(len(sh(["git", "worktree", "list"], self.repo).splitlines()), 4)

    def test_resume_detects_moved_target_altered_package_and_changed_worktree(self):
        base = self.base_repo()
        self.target_repo()
        run_dir = transfer.prepare(self.opts(base=base, target="main", plan=self.three_groups()))

        self.write(self.repo, "src/later.txt", "after prepare\n")
        self.commit("later work")
        payload = os.path.join(run_dir, "groups", "01", "payload", "src", "a.txt")
        with open(payload, "w") as handle:
            handle.write("tampered\n")
        resumed = transfer.resume(run_dir)
        details = " | ".join(d["detail"] for d in resumed["drift_before"])
        self.assertIn("target ref 'main' now resolves", details)
        self.assertIn("package file was altered: src/a.txt", details)
        self.assertEqual(resumed["actions"], ["rewrote 1 package file(s) for group 01"])
        self.assertEqual(open(payload).read(), "alpha\nbeta\n")
        self.assertFalse(os.path.exists(os.path.join(run_dir, "groups", "01", "payload", "src", "later.txt")))

        self.write(os.path.join(run_dir, "worktrees", "group-02"), "src/a.txt", "edited in worktree\n")
        with self.assertRaises(G2SError) as ctx:
            transfer.resume(run_dir)
        self.assertIn("worktree has local changes", str(ctx.exception))
        self.assertEqual(transfer.verify(run_dir)["summary"]["failed"], 1)

    def test_recorded_revision_is_not_replaced_silently(self):
        base = self.base_repo()
        self.target_repo()
        run_dir = transfer.prepare(self.opts(base=base, plan=self.three_groups()))
        self.assertEqual(transfer.record_revision(run_dir, "01", "r7")["status"], "user_provided")
        with self.assertRaises(G2SError):
            transfer.record_revision(run_dir, "01", 8)
        self.assertEqual(transfer.record_revision(run_dir, "01", 8, replace=True)["revision"], 8)
        with self.assertRaises(G2SError):
            transfer.record_revision(run_dir, "02", 8)
        mapping = open(os.path.join(run_dir, "SVN_MANUAL_TRANSFER.md"), encoding="utf-8").read()
        self.assertIn("| r8 | user provided |", mapping)


class Cleanup(TransferTestCase):
    def test_cleanup_lists_by_default_and_never_touches_what_the_run_did_not_create(self):
        base = self.base_repo()
        self.target_repo()
        own_worktree = os.path.join(self.tmp, "users-own-worktree")
        self.git("branch", "users-own-branch")
        self.git("worktree", "add", "-q", own_worktree, "users-own-branch")
        run_dir = transfer.prepare(self.opts(base=base, plan=self.three_groups()))
        other_run = transfer.prepare(self.opts(base=base, plan=self.plan([self.group("everything", ["**"])])))

        listed = transfer.cleanup(run_dir)
        self.assertEqual(len([i for i in listed["would_remove"] if i["kind"] == "worktree"]), 3)
        self.assertEqual(len(sh(["git", "worktree", "list"], self.repo).splitlines()), 6, "listing removes nothing")

        dirty = os.path.join(run_dir, "worktrees", "group-02")
        self.write(dirty, "unsaved.txt", "work in progress\n")
        with_leftovers = transfer.verify(run_dir)
        self.assertEqual(with_leftovers["summary"]["failed"], 0, "untracked output is reported, not a failure")
        self.assertTrue(any("untracked files" in r["detail"] for r in with_leftovers["results"]))
        report = transfer.cleanup(run_dir, execute=True)
        self.assertTrue(any("unsaved changes or untracked files (unsaved.txt)" in r for r in report["refused"]))
        self.assertTrue(os.path.isfile(os.path.join(dirty, "unsaved.txt")))
        self.assertEqual(len([i for i in report["removed"] if i["kind"] == "worktree"]), 2)
        branch = read_json(os.path.join(run_dir, "manifest.json"))["branch"]
        self.assertNotEqual(self.git("branch", "--list", branch), "", "branch stays while a worktree remains")

        os.remove(os.path.join(dirty, "unsaved.txt"))
        report = transfer.cleanup(run_dir, execute=True)
        self.assertEqual(report["refused"], [])
        self.assertEqual(self.git("branch", "--list", branch), "")
        for kept in ("manifest.json", "GROUPS.md", "SVN_MANUAL_TRANSFER.md", "plan.json",
                     os.path.join("groups", "01", "payload", "src", "a.txt")):
            self.assertTrue(os.path.isfile(os.path.join(run_dir, kept)), kept)

        # The user's worktree and branch, and the other run, are intact.
        self.assertTrue(os.path.isdir(own_worktree))
        self.assertNotEqual(self.git("branch", "--list", "users-own-branch"), "")
        self.assertTrue(os.path.isdir(os.path.join(other_run, "worktrees", "group-01")))
        self.assertNotEqual(self.git("branch", "--list", read_json(os.path.join(other_run, "manifest.json"))["branch"]), "")
        self.assertEqual(len(sh(["git", "worktree", "list"], self.repo).splitlines()), 3)
        self.assertEqual(transfer.resume(run_dir)["actions"], [], "a cleaned run is not rebuilt by resume")


class PlanAndSpecialCases(TransferTestCase):
    def test_plan_must_partition_the_delta(self):
        base = self.base_repo()
        self.target_repo()

        def prepare(groups):
            return transfer.prepare(self.opts(base=base, plan=self.plan(groups)))

        with self.assertRaises(G2SError) as ctx:
            prepare([self.group("core", ["src/"])])
        self.assertIn("unassigned: assets/data.bin", str(ctx.exception))
        with self.assertRaises(G2SError) as ctx:
            prepare([self.group("a", ["src/", "**"]), self.group("b", ["src/"])])
        self.assertIn("ambiguous: src/a.txt", str(ctx.exception))
        with self.assertRaises(G2SError) as ctx:
            prepare([self.group("a", ["**"], ["b"]), self.group("b", ["src/"])])
        self.assertIn("not an earlier group", str(ctx.exception))
        with self.assertRaises(G2SError) as ctx:
            prepare([self.group("a", ["**"]), self.group("b", ["nothing-here/"])])
        self.assertIn("matches no changed path", str(ctx.exception))
        self.assertFalse(os.path.isdir(self.out) and any(
            os.path.isdir(os.path.join(self.out, d)) for d in os.listdir(self.out)))

    def test_file_replaced_by_directory(self):
        self.write(self.repo, "thing", "a file\n")
        self.write(self.repo, "keep.txt", "keep\n")
        base = self.commit("base")
        self.make_svn()
        os.remove(os.path.join(self.repo, "thing"))
        self.write(self.repo, "thing/inner.txt", "now a directory\n")
        self.commit("file becomes directory")

        with self.assertRaises(G2SError) as ctx:
            transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.plan([
                self.group("add-first", ["thing/inner.txt"]), self.group("delete-later", ["thing"])])))
        self.assertIn("both a file and the directory", str(ctx.exception))
        self.assertFalse(any(os.path.isdir(os.path.join(self.out, d)) for d in os.listdir(self.out)),
                         "a refused plan must leave no run directory")

        run_dir = transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.plan([self.group("swap", ["thing"])])))
        applied = transfer.apply_group(run_dir, "01", self.wc, SVN_BIN, execute=True, allow_not_ready=True)
        self.assertEqual(applied["post_check"]["status"], "passed", applied["post_check"])
        revision = self.svn_commit("swap")
        self.assertEqual(transfer.record_revision(run_dir, "01", revision, self.wc, SVN_BIN)["status"], "verified")
        with open(os.path.join(self.wc, "thing", "inner.txt")) as handle:
            self.assertEqual(handle.read(), "now a directory\n")

    def test_rename_split_across_groups_becomes_delete_and_add(self):
        base = self.base_repo()
        self.target_repo()
        run_dir = transfer.prepare(self.opts(base=base, plan=self.plan([
            self.group("removals", ["src/old_name.txt", "to_delete.txt"]),
            self.group("rest", ["**"])])))
        manifest = read_json(os.path.join(run_dir, "manifest.json"))
        self.assertTrue(any("split across groups" in n for n in manifest["notes"]))
        self.assertEqual(open(os.path.join(run_dir, "groups", "02", "rename.tsv")).read(), "")
        self.assertIn("src/old_name.txt\n", open(os.path.join(run_dir, "groups", "01", "delete.txt")).read())
        self.assertEqual(transfer.verify(run_dir)["summary"]["failed"], 0)

    def test_special_cases_are_reported_and_never_packaged(self):
        base = self.base_repo()
        self.write(self.repo, "text/windows.txt", b"a\r\nb\r\n")
        self.write(self.repo, "big/model.bin", b"version https://git-lfs.github.com/spec/v1\noid sha256:0\nsize 1\n")
        self.write(self.repo, "config/.env", "TOKEN=not-a-real-value\n")
        sh(["git", "add", "-A"], self.repo)
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=self.repo, input=b"../src/a.txt",
                              stdout=subprocess.PIPE, check=True).stdout.decode().strip()
        sh(["git", "update-index", "--add", "--cacheinfo", "120000,%s,links/pointer" % blob], self.repo)
        sh(["git", "update-index", "--add", "--cacheinfo", "160000,%s,vendor/lib" % base], self.repo)
        sh(["git", "commit", "-q", "-m", "special"], self.repo)
        plan = self.plan([self.group("all", ["**"])])

        report = transfer.inspect(self.opts(base=base))
        self.assertTrue(any("look like secrets" in b for b in report["blockers"]))
        self.assertEqual({m["path"] for m in report["manual_items"]},
                         {"big/model.bin", "links/pointer", "vendor/lib"})
        self.assertIn("crlf", {w["code"] for w in report["warnings"]})
        with self.assertRaises(G2SError):
            transfer.prepare(self.opts(base=base, plan=plan))

        run_dir = transfer.prepare(self.opts(base=base, plan=plan, exclude=["config/.env"]))
        payload = os.path.join(run_dir, "groups", "01", "payload")
        self.assertTrue(os.path.isfile(os.path.join(payload, "text", "windows.txt")))
        self.assertEqual(open(os.path.join(payload, "text", "windows.txt"), "rb").read(), b"a\r\nb\r\n")
        for absent in ("big/model.bin", "links/pointer", "vendor/lib", "config/.env"):
            self.assertFalse(os.path.lexists(os.path.join(payload, *absent.split("/"))), absent)
        manual = open(os.path.join(run_dir, "groups", "01", "manual.txt"), encoding="utf-8").read()
        for path in ("big/model.bin", "links/pointer", "vendor/lib"):
            self.assertIn(path, manual)
        verification = transfer.verify(run_dir)
        self.assertEqual(verification["summary"]["failed"], 0, verification["results"])
        statuses = {r["check"]: r["status"] for r in verification["results"]}
        self.assertEqual(statuses["manual items"], "not_run")
        self.assertEqual(statuses["final snapshot equals the target within scope"], "passed")

    def test_scope_and_uncommitted_changes(self):
        base = self.base_repo()
        self.target_repo()
        self.write(self.repo, "src/a.txt", "alpha\nbeta\nuncommitted\n")
        self.write(self.repo, "src/untracked.txt", "untracked\n")
        before = self.source_state()
        plan = self.plan([self.group("src", ["src/"])])

        without = transfer.inspect(self.opts(base=base, include=["src/"]))
        self.assertNotIn("src/untracked.txt", [o["path"] for o in without["operations"]])
        self.assertTrue(without["source_has_uncommitted_changes"])

        run_dir = transfer.prepare(self.opts(base=base, include=["src/"], include_uncommitted=True, plan=plan))
        self.assertEqual(self.source_state(), before, "index, branch and working tree must be untouched")
        payload = os.path.join(run_dir, "groups", "01", "payload", "src")
        self.assertEqual(open(os.path.join(payload, "a.txt")).read(), "alpha\nbeta\nuncommitted\n")
        self.assertTrue(os.path.isfile(os.path.join(payload, "untracked.txt")))
        worktree = os.path.join(run_dir, "worktrees", "group-01")
        self.assertTrue(os.path.isfile(os.path.join(worktree, "to_delete.txt")), "out-of-scope paths stay at base")
        self.assertFalse(os.path.exists(os.path.join(worktree, "assets", "img.bin")))
        verification = transfer.verify(run_dir)
        self.assertEqual(verification["summary"]["failed"], 0, verification["results"])
        manifest = read_json(os.path.join(run_dir, "manifest.json"))
        self.assertIn("assets/img.bin", manifest["out_of_scope"])
        self.assertEqual(sha256_file(os.path.join(payload, "a.txt")),
                         [o["sha256"] for o in manifest["groups"][0]["operations"] if o["path"] == "src/a.txt"][0])


class ReadinessOfEveryCumulativeState(TransferTestCase):
    """A group is ready only when its own cumulative state passes the required checks."""

    CHECK = "python3 -B tools/check.py"

    def project(self):
        self.write(self.repo, "tools/check.py",
                   "import pathlib, runpy, sys\n"
                   "sys.path.insert(0, '.')\n"
                   "pathlib.Path('build-output.tmp').write_text('left by the check')\n"
                   "runpy.run_path('app/main.py')\n"
                   "sys.exit(0 if pathlib.Path('config.txt').read_text().strip() == 'mode=on' else 1)\n")
        self.write(self.repo, "app/main.py", "print('v1')\n")
        self.write(self.repo, "config.txt", "mode=on\n")
        base = self.commit("base")
        self.write(self.repo, "lib/helper.py", "value = 2\n")
        self.write(self.repo, "app/main.py", "from lib.helper import value\nprint('v%d' % value)\n")
        self.write(self.repo, "docs/notes.md", "# notes\n")
        self.commit("feature that needs the helper")
        return base

    def plan_with(self, order, checks=None):
        groups = {"app": self.group("app", ["app/"]), "lib": self.group("lib", ["lib/"]),
                  "docs": self.group("docs", ["docs/"])}
        path = os.path.join(self.tmp, "plan-%s.json" % "-".join(order))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "groups": [groups[k] for k in order],
                       "checks": checks if checks is not None else [
                           {"name": "smoke", "kind": "smoke", "command": self.CHECK, "required": True}]}, handle)
        return path

    def states(self, run_dir):
        return {g["id"]: g["readiness"]["status"] for g in read_json(os.path.join(run_dir, "manifest.json"))["groups"]}

    def test_a_group_that_does_not_run_is_failed_and_the_regrouped_run_is_ready(self):
        base = self.project()
        # 'app' before 'lib': after group 01 the application imports a module that is not there yet.
        broken = transfer.prepare(self.opts(base=base, plan=self.plan_with(["app", "lib", "docs"])))
        self.assertEqual(set(self.states(broken).values()), {"unverified"}, "nothing is ready before checks run")
        verification = transfer.verify(broken, run_checks=True)
        self.assertEqual(self.states(broken), {"01": "failed", "02": "ready", "03": "ready"})
        self.assertFalse(verification["run_ready"])
        self.assertGreaterEqual(verification["summary"]["failed"], 1)
        log = [r for r in read_json(os.path.join(broken, "manifest.json"))["check_results"] if r["group"] == "01"][0]
        with open(os.path.join(broken, log["log"]), encoding="utf-8") as handle:
            self.assertIn("ModuleNotFoundError", handle.read(), "the log says why it failed")

        self.make_svn_at(base)
        with self.assertRaises(G2SError) as ctx:
            transfer.apply_group(broken, "01", self.wc, SVN_BIN, allow_not_ready=True)
        self.assertIn("never applied", str(ctx.exception))

        # Move the boundary: the dependency goes first. The failed run is kept, and marked.
        fixed = transfer.prepare(self.opts(base=base, plan=self.plan_with(["lib", "app", "docs"]), supersedes=broken))
        verification = transfer.verify(fixed, run_checks=True)
        self.assertEqual(self.states(fixed), {"01": "ready", "02": "ready", "03": "ready"})
        self.assertTrue(verification["run_ready"])
        self.assertEqual(read_json(os.path.join(broken, "manifest.json"))["superseded_by"], fixed)
        with self.assertRaises(G2SError) as ctx:
            transfer.apply_group(broken, "02", self.wc, SVN_BIN)
        self.assertIn("superseded", str(ctx.exception))
        with open(os.path.join(fixed, "GROUPS.md"), encoding="utf-8") as handle:
            self.assertIn("| **ready** | not run |", handle.read())

        # The checks left output in the worktrees: cleanup refuses unless told to discard it.
        refused = transfer.cleanup(broken, execute=True)
        self.assertEqual(len(refused["refused"]), 4, refused)
        removed = transfer.cleanup(broken, execute=True, discard_untracked=True)
        self.assertEqual((removed["refused"], len(removed["removed"])), ([], 4))

    def test_missing_tools_are_blocked_and_states_without_a_check_are_unverified(self):
        base = self.project()
        checks = [
            {"name": "needs-tool", "kind": "build", "command": self.CHECK, "requires": ["g2s-no-such-tool"],
             "from_group": "app"},
            {"name": "optional-fails", "kind": "lint", "command": "python3 -B -c \"raise SystemExit(3)\"",
             "required": False},
        ]
        run_dir = transfer.prepare(self.opts(base=base, plan=self.plan_with(["lib", "app", "docs"], checks)))
        verification = transfer.verify(run_dir, run_checks=True)
        self.assertEqual(self.states(run_dir), {"01": "unverified", "02": "blocked", "03": "blocked"})
        self.assertEqual(verification["summary"]["failed"], 0, "blocked and unverified are not failures")
        self.assertFalse(verification["run_ready"])
        manifest = read_json(os.path.join(run_dir, "manifest.json"))
        self.assertIn("no required check applies", manifest["groups"][0]["readiness"]["reasons"][0])
        self.assertIn("g2s-no-such-tool", manifest["groups"][1]["readiness"]["reasons"][0])
        self.assertTrue(any("does not gate readiness" in r["detail"] for r in verification["results"]))

        for command, expected in (("g2s-no-such-command --version", "command not found"),
                                  ("python3 -B -c \"raise SystemExit(77)\"", "missing prerequisite")):
            other = transfer.prepare(self.opts(base=base, required_check=[command],
                                               plan=self.plan_with(["lib", "app", "docs"], [])))
            transfer.verify(other, run_checks=True)
            first = read_json(os.path.join(other, "manifest.json"))["groups"][0]["readiness"]
            self.assertEqual(first["status"], "blocked")
            self.assertIn(expected, first["reasons"][0])

        self.make_svn_at(base)
        with self.assertRaises(G2SError) as ctx:
            transfer.apply_group(run_dir, "01", self.wc, SVN_BIN)
        self.assertIn("'unverified', not ready", str(ctx.exception))
        self.assertFalse(transfer.apply_group(run_dir, "01", self.wc, SVN_BIN, allow_not_ready=True)["executed"])
        with self.assertRaises(G2SError) as ctx:
            transfer.prepare(self.opts(base=base, plan=self.plan_with(["lib", "app", "docs"], [
                {"name": "x", "command": "true", "from_group": "nope", "kind": "magic"}])))
        self.assertIn("'from_group' names no group", str(ctx.exception))
        self.assertIn("'kind' must be one of", str(ctx.exception))

    def make_svn_at(self, base):
        """An SVN repository holding the base commit's files."""
        self.make_svn(seed_from_repo=False)
        for path, data in self.target_files(base).items():
            self.write(self.wc, path, data)
        self.svn_run("add", "-q", "--force", ".")
        self.svn_commit("seed")

    def test_the_working_copy_is_checked_again_because_the_worktree_does_not_prove_it(self):
        base = self.project()
        self.make_svn_at(base)
        # SVN drifted in a file the transfer never touches, so no baseline check can see it.
        self.write(self.wc, "config.txt", "mode=off\n")
        self.svn_commit("an svn-only configuration change")

        run_dir = transfer.prepare(self.opts(base=base, svn_wc=self.wc, plan=self.plan_with(["lib", "app", "docs"])))
        transfer.verify(run_dir, run_checks=True)
        self.assertEqual(self.states(run_dir)["01"], "ready", "the Git worktree passes")

        nothing = transfer.precommit(run_dir, "01", self.wc, SVN_BIN)
        self.assertEqual(nothing["status"], "failed")
        self.assertIn("nothing is staged", nothing["reasons"][0])

        transfer.apply_group(run_dir, "01", self.wc, SVN_BIN, execute=True)
        staged = transfer.precommit(run_dir, "01", self.wc, SVN_BIN)
        self.assertEqual(staged["status"], "failed", "the same check fails where it will be committed")
        self.assertFalse(staged["identical_to_git_worktree"])
        self.assertEqual(staged["differences_outside_transfer"], [{"path": "config.txt", "difference": "content"}])
        self.assertEqual(staged["unversioned_files_created_by_checks"], ["build-output.tmp"])
        self.assertEqual(self.svn.status(self.wc)["lib/helper.py"]["item"], "added", "nothing was reverted or committed")
        os.remove(os.path.join(self.wc, "build-output.tmp"))

        skipped = transfer.precommit(run_dir, "01", self.wc, SVN_BIN, skip_checks=True)
        self.assertEqual(skipped["status"], "unverified", "comparing content alone never earns ready")

        self.write(self.wc, "lib/helper.py", "value = 999\n")
        tampered = transfer.precommit(run_dir, "01", self.wc, SVN_BIN)
        self.assertEqual(tampered["status"], "failed")
        self.assertTrue(any("lib/helper.py is different compared with the package" in r for r in tampered["reasons"]))

        self.write(self.wc, "lib/helper.py", "value = 2\n")
        self.write(self.wc, "docs/stray.md", "not part of the group\n")
        self.svn_run("add", "-q", "--parents", "docs/stray.md")
        stray = transfer.precommit(run_dir, "01", self.wc, SVN_BIN)
        self.assertTrue(any("docs/stray.md is staged" in r and "does not belong to group 01" in r
                            for r in stray["reasons"]), stray["reasons"])

        self.svn_run("revert", "-q", "-R", "docs")
        shutil.rmtree(os.path.join(self.wc, "docs"), onerror=_force_remove)
        revision = self.svn_commit("committed although the working copy was not ready")
        recorded = transfer.record_revision(run_dir, "01", revision, self.wc, SVN_BIN)
        self.assertTrue(any("not shown ready before this commit" in d for d in recorded["details"]))


if __name__ == "__main__":
    unittest.main()
