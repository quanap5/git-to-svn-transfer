"""Progress reporting: stderr only, plain unless a terminal, and never part of the JSON result."""
import io
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
sys.path.insert(0, SCRIPTS)

from g2s import progress  # noqa: E402

SCRIPT = os.path.join(SCRIPTS, "git_to_svn.py")


class Stream(io.StringIO):
    def __init__(self, encoding="utf-8", terminal=False):
        super().__init__()
        self._encoding, self._terminal = encoding, terminal

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return self._terminal


class ReporterTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(progress.configure, "off")
        self.environ = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.environ)))
        os.environ.pop("NO_COLOR", None)
        os.environ["TERM"] = "xterm"

    def report(self, mode, **stream_options):
        stream = Stream(**stream_options)
        progress.configure(mode, stream)
        progress.start("verify", "run-1")
        progress.plan(2)
        progress.step("structure")
        progress.item("no operation missing", "passed")
        progress.step("project checks")
        progress.running("group 01")
        progress.item("group 01", "failed", "exit 1")
        progress.item("group 02", "blocked")
        progress.finish(1, "01 failed")
        return stream.getvalue()

    def test_plain_text_when_stderr_is_not_a_terminal(self):
        text = self.report("auto")
        self.assertNotIn("\033", text)
        self.assertNotIn("\r", text)
        self.assertIn("[verify] run-1", text)
        self.assertIn(" 1/2 structure", text)
        self.assertIn(" 2/2 project checks", text)
        self.assertIn("group 01  failed  exit 1", text)
        self.assertIn("exit 1", text.splitlines()[-1])

    def test_colour_and_a_replaced_running_line_on_a_terminal(self):
        text = self.report("auto", terminal=True)
        self.assertIn("\033[32m", text)  # passed
        self.assertIn("\033[31m", text)  # failed
        self.assertIn("\033[33m", text)  # blocked
        self.assertIn("\r\033[K", text)

    def test_no_colour_is_respected_and_plain_never_colours(self):
        os.environ["NO_COLOR"] = "1"
        self.assertNotIn("\033[3", self.report("auto", terminal=True))
        del os.environ["NO_COLOR"]
        self.assertNotIn("\033", self.report("plain", terminal=True))

    def test_ascii_marks_when_the_stream_cannot_encode_the_glyphs(self):
        text = self.report("auto", encoding="cp949")
        text.encode("ascii")
        self.assertIn("OK no operation missing", text)
        self.assertIn("FAIL group 01", text)

    def test_off_prints_nothing(self):
        self.assertEqual(self.report("off"), "")

    def test_a_broken_stream_is_ignored(self):
        stream = Stream()
        progress.configure("plain", stream)
        stream.close()
        progress.start("verify")
        progress.item("anything", "passed")


@unittest.skipIf(shutil.which("git") is None, "git is required")
class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="g2s-progress-")
        self.addCleanup(self.remove)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            self.git("config", key, value)
        self.commit({"readme.txt": "base\n"})
        self.base = self.git("rev-parse", "HEAD").strip()
        self.commit({"a/one.txt": "one\n", "b/two.txt": "two\n"})
        python = shlex.quote(sys.executable)
        self.plan = os.path.join(self.tmp, "plan.json")
        with open(self.plan, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "checks": [
                {"name": "always", "kind": "build", "command": python + " -c pass"},
                {"name": "broken", "kind": "test", "command": python + " -c 'raise SystemExit(1)'",
                 "from_group": "b"}],
                "groups": [self.group("a", []), self.group("b", ["a"])]}, handle)

    def remove(self):
        if os.path.isdir(os.path.join(self.repo, ".git")):
            subprocess.run(["git", "worktree", "prune"], cwd=self.repo)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def group(self, key, depends_on):
        return {"key": key, "title": key, "rationale": "Belongs together.", "message": "feat(%s): transfer" % key,
                "paths": [key + "/"], "depends_on": depends_on}

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
        proc = subprocess.run([sys.executable, SCRIPT] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def test_progress_goes_to_stderr_and_stdout_stays_json(self):
        source = ["--repo", self.repo, "--base", self.base, "--plan", self.plan]
        code, out, err = self.cli("prepare", *source, "--out", os.path.join(self.tmp, "out"))
        self.assertEqual(code, 0, err)
        run = json.loads(out)["run"]
        self.assertNotIn("\033", err)
        for expected in ("[prepare]", " 1/5 ", " 5/5 guides", "group 01 a  created", "group 02  created"):
            self.assertIn(expected, err)

        code, out, err = self.cli("verify", "--run", run, "--run-checks")
        self.assertEqual(code, 1, err)
        self.assertEqual(json.loads(out)["readiness"], {"01": "ready", "02": "failed"})
        for expected in ("[verify]", " 5/5 readiness", "always  passed", "broken  failed", "group 01 a  ready",
                         "group 02 b  failed", "exit 1"):
            self.assertIn(expected, err)

        code, out, err = self.cli("verify", "--run", run, "--progress", "off")
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["readiness"], {"01": "ready", "02": "failed"})

        code, out, err = self.cli("cleanup", "--run", run, "--execute", "--discard-untracked")
        self.assertEqual(code, 0, err)
        self.assertIn("removed", err)

    def test_an_error_still_ends_with_the_error_line(self):
        code, out, err = self.cli("verify", "--run", os.path.join(self.tmp, "missing"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.strip().splitlines()[-1].startswith("error: "), err)


if __name__ == "__main__":
    unittest.main()
