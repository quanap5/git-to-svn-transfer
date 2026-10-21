"""'next': the step it names follows from the manifest alone, and it changes nothing."""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "git-to-svn-transfer", "scripts")
SCRIPT = os.path.join(SCRIPTS, "git_to_svn.py")


@unittest.skipIf(shutil.which("git") is None, "git is required")
class NextStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="g2s-next-")
        self.addCleanup(self.remove)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            self.git("config", key, value)
        self.commit({"readme.txt": "base\n", "a/old.txt": "a long enough line to be found as a rename\n" * 5,
                     "a/gone.txt": "gone\n"})
        self.base = self.git("rev-parse", "HEAD").strip()
        self.git("mv", "a/old.txt", "a/new.txt")
        self.git("rm", "-q", "a/gone.txt")
        self.write({"a/added.txt": "one\n", "b/two.txt": "two\n", "readme.txt": "changed\n", "b/run.sh": "echo\n"})
        self.git("add", "-A")
        self.git("update-index", "--chmod=+x", "b/run.sh")
        self.git("commit", "-q", "-m", "change")
        plan = os.path.join(self.tmp, "plan.json")
        with open(plan, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1,
                       "checks": [{"name": "always", "kind": "build", "command": shlex.quote(sys.executable) + " -c pass"}],
                       "groups": [self.group("a", ["a/"], []), self.group("b", ["b/", "readme.txt"], ["a"])]}, handle)
        code, out, err = self.cli("prepare", "--repo", self.repo, "--base", self.base, "--plan", plan,
                                  "--out", os.path.join(self.tmp, "out"))
        self.assertEqual(code, 0, err)
        self.run_dir = json.loads(out)["run"]
        self.manifest_path = os.path.join(self.run_dir, "manifest.json")

    def remove(self):
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def group(self, key, paths, depends_on):
        return {"key": key, "title": key, "rationale": "Belongs together.", "message": "feat(%s): transfer" % key,
                "paths": paths, "depends_on": depends_on}

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.repo, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout.decode("utf-8")

    def write(self, files):
        for path, text in files.items():
            full = os.path.join(self.repo, *path.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)

    def commit(self, files):
        self.write(files)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")

    def cli(self, *args):
        proc = subprocess.run([sys.executable, SCRIPT] + list(args) + ["--progress", "off"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def next(self, *extra):
        code, out, err = self.cli("next", "--run", self.run_dir, *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def edit(self, change):
        with open(self.manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        change(manifest)
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)

    def digest(self):
        with open(self.manifest_path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def verify_checks(self):
        code, _, err = self.cli("verify", "--run", self.run_dir, "--run-checks")
        self.assertEqual(code, 0, err)

    @staticmethod
    def titles(result):
        return [(s["by"], s["title"]) for s in result["steps"]]

    def test_checks_come_first_and_next_changes_nothing(self):
        before = self.digest()
        events = os.path.getsize(os.path.join(self.run_dir, "events.jsonl"))
        result = self.next()
        self.assertEqual(result["stage"], "verify")
        self.assertIn("--run-checks", result["steps"][0]["commands"][0])
        self.assertEqual(self.digest(), before)
        self.assertEqual(os.path.getsize(os.path.join(self.run_dir, "events.jsonl")), events,
                         "next is a question, not a command on the run")

    def test_stage_names_only_what_the_group_needs(self):
        self.verify_checks()
        result = self.next("--svn-wc", os.path.join(self.tmp, "wc"))
        self.assertEqual((result["stage"], result["progress"]["next_group"]), ("stage", "01"))
        self.assertIsNone(result["decision"])
        self.assertEqual(result["files"]["rename"], [{"from": "a/old.txt", "to": "a/new.txt"}])
        self.assertEqual(result["files"]["delete"], ["a/gone.txt"])
        self.assertIn("N=01", result["setup"])
        self.assertIn("WC=%s" % shlex.quote(os.path.join(self.tmp, "wc")), result["setup"])
        titles = [t for _, t in self.titles(result)]
        self.assertIn("Rename with svn move, so history follows the file", titles)
        self.assertNotIn("Set or clear the executable property", titles, "group 01 has no executable file")
        # The user changes the working copy; the agent only looks and checks.
        for by, title in self.titles(result):
            commands = " ".join(next(s for s in result["steps"] if s["title"] == title)["commands"])
            if by == "agent":
                for word in ("cp ", " add ", " delete ", " move ", " commit", " update"):
                    self.assertNotIn(word, commands, title)
        self.assertEqual(result["steps"][-1]["by"], "agent")
        self.assertIn("precommit", result["steps"][-1]["commands"][0])

    def test_the_working_copy_is_named_missing_when_unknown(self):
        self.verify_checks()
        result = self.next()
        self.assertTrue(result["missing"])
        self.assertTrue(any(line.startswith("WC=") and "path/to" in line for line in result["setup"]))

    def test_the_steps_follow_precommit_and_record(self):
        self.verify_checks()

        def staged(status):
            def change(m):
                m["groups"][0]["svn_precommit"] = {"status": status, "reasons": ["because"]}
            return change

        self.edit(staged("failed"))
        result = self.next()
        self.assertEqual(result["stage"], "fix_precommit")
        self.assertEqual(result["steps"][0]["by"], "you")
        self.assertIn('"$SVN" revert -R .', result["steps"][0]["commands"])

        self.edit(staged("ready"))
        result = self.next()
        self.assertEqual(result["stage"], "commit")
        self.assertEqual([by for by, _ in self.titles(result)], ["agent", "you", "agent"])
        self.assertIn("commit -F", result["steps"][1]["commands"][0])

        self.edit(staged("unverified"))
        self.assertIn("user's decision", self.next()["decision"])

        def revision(status, number=7):
            def change(m):
                m["groups"][0]["svn_revision"] = {"revision": number, "status": status, "recorded_at": None,
                                                  "details": ["extra path"]}
            return change

        self.edit(revision("user_provided"))
        result = self.next()
        self.assertEqual(result["stage"], "verify_record")
        self.assertIn("--revision 7", result["steps"][0]["commands"][1])

        self.edit(revision("mismatch"))
        result = self.next()
        self.assertEqual(result["stage"], "resolve_mismatch")
        self.assertIn("extra path", result["why"])
        self.assertTrue(result["decision"])

        self.edit(revision("verified"))
        result = self.next()
        self.assertEqual((result["stage"], result["progress"]["next_group"]), ("stage", "02"))
        self.assertIn("r7", result["steps"][1]["expect"], "group 02 starts from group 01's revision")
        self.assertIn('"$SVN" propset svn:executable ON b/run.sh',
                      next(s for s in result["steps"] if s["title"] == "Set or clear the executable property")["commands"])

    def test_after_the_last_group(self):
        self.verify_checks()

        def all_verified(m):
            for number, group in enumerate(m["groups"], 7):
                group["svn_revision"] = {"revision": number, "status": "verified", "recorded_at": None, "details": []}
        self.edit(all_verified)
        self.assertEqual(self.next()["stage"], "final_verify")

        def final_passed(m):
            m["verification"]["results"].append({"check": "SVN working copy holds the final state",
                                                 "status": "passed"})
        self.edit(final_passed)
        result = self.next()
        self.assertEqual(result["stage"], "cleanup")
        self.assertEqual([by for by, _ in self.titles(result)], ["agent", "you"])

        self.edit(lambda m: m.update(cleanup={"worktrees_removed": True}))
        result = self.next()
        self.assertEqual((result["stage"], result["steps"]), ("done", []))

    def test_a_failed_group_stops_the_transfer(self):
        self.verify_checks()
        self.edit(lambda m: m["groups"][1]["readiness"].update(status="failed", reasons=["test failed"]))
        result = self.next()
        self.assertEqual(result["stage"], "fix_plan")
        self.assertIn("02", result["why"])
        self.assertTrue(result["decision"])

    def test_a_superseded_run_points_at_its_replacement(self):
        self.edit(lambda m: m.update(superseded_by="/elsewhere/run"))
        result = self.next()
        self.assertEqual(result["stage"], "superseded")
        self.assertIn("/elsewhere/run", result["steps"][0]["commands"][0])

    def test_not_a_run(self):
        code, _, err = self.cli("next", "--run", os.path.join(self.tmp, "missing"))
        self.assertEqual(code, 2)
        self.assertIn("no manifest.json", err)


if __name__ == "__main__":
    unittest.main()
