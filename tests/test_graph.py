"""'graph': an SVN repository's history as lanes, forks and merges in one page, without changing SVN."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "git-to-svn-transfer", "scripts")
SCRIPT = os.path.join(SCRIPTS, "git_to_svn.py")
sys.path.insert(0, SCRIPTS)

from g2s import svngraph  # noqa: E402
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


def path(p, action="M", kind="file", copy=None, props=False):
    return {"path": p, "action": action, "kind": kind, "copyfrom_path": copy[0] if copy else None,
            "copyfrom_rev": copy[1] if copy else None, "text_mods": action != "D", "prop_mods": props}


def entry(rev, *paths, message=""):
    return {"revision": rev, "author": "dev", "date": "2026-10-01T00:00:%02dZ" % rev,
            "message": message or "r%d" % rev, "paths": list(paths)}


REPO = {"root": "file:///repo", "uuid": "u", "head": 9}


class LanesTest(unittest.TestCase):
    def test_lane_of_follows_the_standard_layout_at_any_depth(self):
        cases = {"/trunk/src/a.c": "/trunk", "/branches/feature/x": "/branches/feature", "/tags/v1": "/tags/v1",
                 "/proj/trunk/a": "/proj/trunk", "/proj/branches/b/c": "/proj/branches/b", "/branches": "/",
                 "/random/file.txt": "/"}
        for given, lane in cases.items():
            self.assertEqual(svngraph.lane_of(given), lane, given)
        self.assertEqual([svngraph.kind_of(l) for l in ("/", "/proj/trunk", "/branches/b", "/tags/v1")],
                         ["root", "trunk", "branch", "tag"])

    def test_mergeinfo_ranges_and_what_a_change_added(self):
        info = svngraph.parse_mergeinfo("/branches/a:3-5,7*\n/trunk:2\nnonsense\n")
        self.assertEqual(info, {"/branches/a": [(3, 5), (7, 7)], "/trunk": [(2, 2)]})
        self.assertEqual(svngraph.newest_added([(3, 9)], [(3, 5)]), 9)
        self.assertEqual(svngraph.newest_added([(3, 9)], [(3, 9)]), None)
        self.assertEqual(svngraph.newest_added([(3, 9)], [(7, 9)]), 6)


class BuildTest(unittest.TestCase):
    def history(self):
        return [
            entry(1, path("/trunk", "A", "dir"), path("/branches", "A", "dir"), path("/tags", "A", "dir")),
            entry(2, path("/trunk/a.txt", "A")),
            entry(3, path("/branches/feature", "A", "dir", ("/trunk", 2))),
            entry(4, path("/branches/feature/a.txt")),
            entry(5, path("/trunk/b.txt", "A")),
            entry(6, path("/tags/v1", "A", "dir", ("/trunk", 5))),
            entry(7, path("/trunk", "M", "dir", props=True), path("/trunk/a.txt")),
            entry(8, path("/branches/feature", "D", "dir")),
        ]

    def mergeinfo(self, lane, rev):
        self.lookups.append((lane, rev))
        return "/branches/feature:3-4\n" if (lane, rev) == ("/trunk", 7) else ""

    def test_lanes_forks_merge_and_close(self):
        self.lookups = []
        data = svngraph.build(REPO, self.history(), self.mergeinfo, limit=100)
        self.assertEqual([r["rev"] for r in data["revisions"]], [8, 7, 6, 5, 4, 3, 2, 1])
        place = {r["rev"]: r["lane"] for r in data["revisions"]}
        self.assertEqual(place[1], "/trunk", "structural directories do not make a lane of their own")
        self.assertEqual((place[3], place[6], place[8]), ("/branches/feature", "/tags/v1", "/branches/feature"))
        lanes = {l["id"]: l for l in data["lanes"]}
        self.assertEqual(set(lanes), {"/trunk", "/branches/feature", "/tags/v1"})
        self.assertEqual(lanes["/trunk"]["column"], 0)
        self.assertEqual(lanes["/branches/feature"]["closed"], 8)
        self.assertNotEqual(lanes["/branches/feature"]["column"], 0)
        self.assertEqual(lanes["/tags/v1"]["column"], 2, "r6 lies inside the branch's r3 to r8, so they cannot share")
        late = svngraph.build(REPO, self.history() + [entry(9, path("/tags/v2", "A", "dir", ("/trunk", 7)))])
        self.assertEqual({l["id"]: l["column"] for l in late["lanes"]}["/tags/v2"], 1,
                         "a tag after the branch was deleted reuses its column")
        forks = [(e["from_lane"], e["from_rev"], e["to_rev"]) for e in data["edges"] if e["kind"] == "fork"]
        self.assertEqual(sorted(forks), [("/trunk", 2, 3), ("/trunk", 5, 6)])
        merges = [e for e in data["edges"] if e["kind"] == "merge"]
        self.assertEqual([(m["from_lane"], m["from_rev"], m["to_rev"], m["source_rev"]) for m in merges],
                         [("/branches/feature", 4, 7, 4)])
        self.assertEqual(self.lookups, [("/trunk", 7), ("/trunk", 6)])
        self.assertEqual(data["counts"], {"revisions": 8, "lanes": 3, "forks": 2, "merges": 1})
        self.assertFalse(data["truncated"])

    def test_an_idle_branch_does_not_widen_the_graph_and_a_recreated_one_is_open(self):
        history = [
            entry(1, path("/trunk/a", "A")),
            entry(2, path("/branches/old", "A", "dir", ("/trunk", 1))),
            entry(3, path("/branches/old/a")),
            entry(4, path("/branches/redo", "A", "dir", ("/trunk", 1))),
            entry(5, path("/branches/redo", "D", "dir")),
            entry(6, path("/branches/redo", "A", "dir", ("/trunk", 1))),
            entry(7, path("/trunk/a")),
            entry(8, path("/branches/new", "A", "dir", ("/trunk", 7))),
            entry(9, path("/branches/new/a")),
        ]
        lanes = {l["id"]: l for l in svngraph.build(REPO, history)["lanes"]}
        self.assertEqual(lanes["/trunk"]["column"], 0)
        self.assertEqual(lanes["/branches/new"]["column"], 1,
                         "'old' was never deleted but its last revision is r3, so its column is free again")
        self.assertIsNone(lanes["/branches/redo"]["closed"], "deleted in r5 but recreated in r6")

    def test_a_fork_from_outside_the_window_is_marked(self):
        data = svngraph.build(REPO, [entry(40, path("/branches/old", "A", "dir", ("/trunk", 12))),
                                     entry(41, path("/trunk/x"))], None, limit=2)
        fork = [e for e in data["edges"] if e["kind"] == "fork"][0]
        self.assertEqual((fork["from_rev"], fork["outside"], fork["source_rev"]), (None, True, 12))
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["notes"]), 2)

    def test_a_repository_without_the_layout_is_one_lane(self):
        data = svngraph.build(REPO, [entry(1, path("/a.txt", "A")), entry(2, path("/docs/b.txt", "A"))])
        self.assertEqual([l["id"] for l in data["lanes"]], ["/"])
        self.assertEqual(data["edges"], [])

    def test_nested_projects_keep_their_prefix(self):
        data = svngraph.build(REPO, [entry(1, path("/proj/trunk/a", "A")),
                                     entry(2, path("/proj/branches/b", "A", "dir", ("/proj/trunk", 1)))])
        self.assertEqual(sorted(l["label"] for l in data["lanes"]), ["proj/branches/b", "proj/trunk"])
        self.assertEqual(data["edges"][0]["from_rev"], 1)


@unittest.skipIf(SKIP, SKIP)
class GraphCommandTest(unittest.TestCase):
    def setUp(self):
        if TMP_ROOT:
            os.makedirs(TMP_ROOT, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix="g2s-graph-", dir=TMP_ROOT)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = Svn(SVN_BIN)
        store = os.path.join(self.tmp, "store")
        self.sh([SVNADMIN_BIN, "create", self.client.native(store)], self.tmp)
        self.url = ("file:///" + self.sh(["wslpath", "-m", store], self.tmp).strip()) if WINDOWS_CLIENT \
            else "file://" + store
        self.svn("mkdir", "-m", "layout", self.url + "/trunk", self.url + "/branches", self.url + "/tags")
        self.trunk = self.checkout("trunk")
        self.write(self.trunk, "a.txt", "one\n")
        self.svn("add", "a.txt", cwd=self.trunk)
        self.svn("commit", "-m", "add a", cwd=self.trunk)
        self.svn("copy", "-m", "branch feature", self.url + "/trunk", self.url + "/branches/feature")
        self.svn("copy", "-m", "tag v1", self.url + "/trunk", self.url + "/tags/v1")
        branch = self.checkout("branches/feature")
        self.write(branch, "a.txt", "one\ntwo\n")
        self.svn("commit", "-m", "work on the branch", cwd=branch)
        self.svn("update", cwd=self.trunk)
        self.svn("merge", "^/branches/feature", cwd=self.trunk)
        self.svn("commit", "-m", "merge feature", cwd=self.trunk)
        self.svn("update", cwd=self.trunk)

    def sh(self, argv, cwd):
        proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode:
            raise AssertionError("%s failed: %s" % (argv, proc.stderr.decode("utf-8", "replace")))
        return proc.stdout.decode("utf-8", "replace")

    def svn(self, *args, **kw):
        return self.sh([SVN_BIN, "--non-interactive", "-q"] + list(args), kw.get("cwd", self.tmp))

    def checkout(self, where):
        wc = os.path.join(self.tmp, where.replace("/", "-"))
        self.svn("checkout", self.url + "/" + where, self.client.native(wc))
        return wc

    def write(self, wc, name, text):
        with open(os.path.join(wc, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def cli(self, *args):
        proc = subprocess.run([sys.executable, SCRIPT] + list(args) + ["--svn-bin", SVN_BIN, "--progress", "off"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def test_graph_of_a_real_repository(self):
        head = self.client.repository(self.url)["head"]
        out = os.path.join(self.tmp, "graph.html")
        code, stdout, err = self.cli("graph", "--svn-wc", self.trunk, "--out", out)
        self.assertEqual(code, 0, err)
        result = json.loads(stdout)
        self.assertEqual((result["revisions"], result["lanes"], result["forks"], result["merges"]), (6, 3, 2, 1))
        self.assertEqual(result["head"], head)
        with open(out, encoding="utf-8") as handle:
            page = handle.read()
        self.assertNotIn("__G2S_DATA__", page)
        data = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1))
        merge = [e for e in data["edges"] if e["kind"] == "merge"][0]
        self.assertEqual((merge["from_lane"], merge["to_lane"], merge["to_rev"]), ("/branches/feature", "/trunk", head))
        self.assertEqual(self.client.repository(self.url)["head"], head, "graph must not change the repository")
        self.assertEqual(self.sh([SVN_BIN, "status"], self.trunk).strip(), "", "nor the working copy")

    def test_the_page_never_goes_inside_the_working_copy(self):
        code, _, err = self.cli("graph", "--svn-wc", self.trunk, "--out", os.path.join(self.trunk, "g.html"))
        self.assertEqual(code, 2)
        self.assertIn("outside the SVN working copy", err)
        code, stdout, err = self.cli("graph", "--url", self.url + "/trunk", "--out", os.path.join(self.tmp, "u.html"),
                                     "--limit", "2")
        self.assertEqual(code, 0, err)
        result = json.loads(stdout)
        self.assertEqual((result["revisions"], result["truncated"]), (2, True))


if __name__ == "__main__":
    unittest.main()
