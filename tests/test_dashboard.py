"""dashboard.html and events.jsonl: derived from the manifest, safe to embed, never part of the transfer."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "git-to-svn-transfer", "scripts")
sys.path.insert(0, SCRIPTS)

from g2s import dashboard  # noqa: E402

SCRIPT = os.path.join(SCRIPTS, "git_to_svn.py")


class EmbedTest(unittest.TestCase):
    def test_data_cannot_close_the_script_element(self):
        value = {"path": "a/</script><script>alert(1)</script>.txt"}
        text = dashboard.embed(value)
        self.assertNotIn("<", text)
        self.assertEqual(json.loads(text), value)


@unittest.skipIf(shutil.which("git") is None, "git is required")
class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="g2s-dashboard-")
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
        self.commit({"a/added.txt": "one\n", "b/two.txt": "two\n", "readme.txt": "changed\n"})
        python = shlex.quote(sys.executable)
        self.plan = os.path.join(self.tmp, "plan.json")
        with open(self.plan, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1,
                       "checks": [{"name": "always", "kind": "build", "command": python + " -c pass"}],
                       "groups": [self.group("a", ["a/"], []), self.group("b", ["b/", "readme.txt"], ["a"])]}, handle)
        code, out, err = self.cli("prepare", "--repo", self.repo, "--base", self.base, "--plan", self.plan,
                                  "--out", os.path.join(self.tmp, "out"))
        self.assertEqual(code, 0, err)
        self.result = json.loads(out)
        self.run_dir = self.result["run"]

    def remove(self):
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def group(self, key, paths, depends_on):
        return {"key": key, "title": key, "rationale": "Belongs together.", "message": "feat(%s): transfer" % key,
                "paths": paths, "depends_on": depends_on}

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.repo, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout.decode("utf-8")

    def commit(self, files):
        for path, text in files.items():
            full = os.path.join(self.repo, *path.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")

    def cli(self, *args):
        proc = subprocess.run([sys.executable, SCRIPT] + list(args) + ["--progress", "off"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def data(self):
        with open(os.path.join(self.run_dir, "dashboard.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertNotIn("__G2S_DATA__", page)
        return json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1))

    def events(self):
        with open(os.path.join(self.run_dir, "events.jsonl"), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_prepare_writes_the_dashboard_with_counts_and_trees(self):
        self.assertEqual(self.result["dashboard"], os.path.join(self.run_dir, "dashboard.html"))
        data = self.data()
        first, second = data["groups"]
        self.assertEqual({k: first["counts"][k] for k in ("add", "modify", "delete", "rename")},
                         {"add": 1, "modify": 0, "delete": 1, "rename": 1})
        self.assertEqual({k: second["counts"][k] for k in ("add", "modify", "delete")},
                         {"add": 1, "modify": 1, "delete": 0})
        self.assertEqual(data["totals"]["add"], 2)
        self.assertEqual(data["base_paths"], ["a/gone.txt", "a/old.txt", "readme.txt"])
        renamed = [op for op in first["ops"] if op["from"]]
        self.assertEqual([(op["p"], op["from"]) for op in renamed], [("a/new.txt", "a/old.txt")])
        self.assertTrue(any(op["p"] == "a/old.txt" and op["to"] == "a/new.txt" for op in first["ops"]))
        self.assertEqual([e["command"] for e in self.events()], ["prepare"])

    def test_every_command_adds_an_event_and_refreshes_the_page(self):
        code, _, err = self.cli("verify", "--run", self.run_dir, "--run-checks")
        self.assertEqual(code, 0, err)
        data = self.data()
        self.assertEqual([g["readiness"]["status"] for g in data["groups"]], ["ready", "ready"])
        self.assertEqual([c["status"] for c in data["groups"][0]["checks"]], ["passed"])
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, *data["groups"][0]["checks"][0]["log"].split("/"))))
        self.assertEqual(data["totals"]["ready"], 2)

        code, _, _ = self.cli("apply", "--run", self.run_dir, "--group", "09", "--svn-wc", self.tmp)
        self.assertEqual(code, 2)
        events = self.events()
        self.assertEqual([(e["command"], e["exit_code"]) for e in events], [("prepare", 0), ("verify", 0), ("apply", 2)])
        self.assertEqual(events[-1]["group"], "09")
        self.assertEqual(len(self.data()["events"]), 3)

    def test_report_rebuilds_the_page_without_adding_an_event(self):
        os.remove(os.path.join(self.run_dir, "dashboard.html"))
        code, out, err = self.cli("report", "--run", self.run_dir)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(json.loads(out)["dashboard"]))
        self.assertEqual(len(self.events()), 1)
        code, _, err = self.cli("report", "--run", os.path.join(self.tmp, "missing"))
        self.assertEqual(code, 2)
        self.assertIn("no manifest.json", err)

    def test_the_dashboard_does_not_count_as_drift(self):
        code, out, err = self.cli("resume", "--run", self.run_dir)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["drift_after"], [])


if __name__ == "__main__":
    unittest.main()
