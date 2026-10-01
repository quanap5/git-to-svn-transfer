"""The scripts must not be able to commit to SVN or push to Git at all."""
import os
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")


def sources():
    for folder, _, names in os.walk(SCRIPTS):
        for name in sorted(names):
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    yield name, handle.read()


class NoCommitOrPush(unittest.TestCase):
    def test_the_svn_wrapper_has_no_committing_verb(self):
        text = dict(sources())["svnops.py"]
        for verb in ("commit", "ci", "import", "copy", "mkdir", "merge", "switch", "relocate"):
            for quote in ('"', "'"):
                self.assertNotIn(quote + verb + quote, text, "svnops.py passes '%s' to svn" % verb)

    def test_no_script_pushes_or_reaches_for_svnmucc(self):
        for name, text in sources():
            for token in ('"push"', "'push'", "svnmucc", '"fetch"', '"pull"'):
                self.assertNotIn(token, text, "%s contains %s" % (name, token))

    def test_only_the_svn_wrapper_runs_svn(self):
        for name, text in sources():
            if name != "svnops.py":
                self.assertNotIn("self.bin", text, name)
                self.assertNotIn(".svn(", text, "%s calls svn directly instead of a named operation" % name)


if __name__ == "__main__":
    unittest.main()
