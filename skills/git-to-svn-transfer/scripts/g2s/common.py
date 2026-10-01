"""Shared helpers: process execution, path safety, pattern matching, JSON I/O."""
import datetime
import fnmatch
import hashlib
import json
import os
import subprocess


class G2SError(Exception):
    """A condition the user must resolve. The CLI prints it and exits 2."""


def run(argv, cwd=None, env=None, stdin=None, check=True):
    """Run a command without a shell. Returns CompletedProcess with bytes output."""
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise G2SError("command not found: %s" % argv[0])
    if check and proc.returncode != 0:
        raise G2SError("command failed (%d): %s\n%s" % (
            proc.returncode, " ".join(argv[:6]) + (" ..." if len(argv) > 6 else ""),
            proc.stderr.decode("utf-8", "replace").strip()))
    return proc


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lf(data):
    return data.replace(b"\r\n", b"\n")


def is_binary(data):
    return b"\0" in data[:8000]


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, value):
    """Write through a temporary file so an interrupted run never leaves half a manifest."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=1, ensure_ascii=False, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


FORBIDDEN_COMPONENTS = {".git", ".svn", "..", "."}
WINDOWS_HOSTILE = set('<>:"|?*\\')


def check_relpath(path):
    """Refuse any path that could escape the working copy or touch VCS metadata."""
    if not path or path.startswith("/") or "\n" in path or "\r" in path or "\0" in path:
        raise G2SError("unsupported path (absolute, empty, or contains a line break): %r" % path)
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        raise G2SError("unsupported path: the file name is not valid UTF-8: %r" % path)
    parts = path.split("/")
    for part in parts:
        if part == "" or part in FORBIDDEN_COMPONENTS:
            raise G2SError("unsafe path component %r in %r" % (part, path))
    return path


def windows_hostile(path):
    return any(ch in WINDOWS_HOSTILE for ch in path) or any(
        part.endswith((" ", ".")) for part in path.split("/"))


def inside(child, parent):
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def wc_path(wc, relpath):
    """Join and prove the result stays inside the working copy."""
    check_relpath(relpath)
    full = os.path.join(wc, *relpath.split("/"))
    if not inside(os.path.dirname(full), wc):
        raise G2SError("path leaves the working copy through a link: %s" % relpath)
    return full


def specificity(pattern, path):
    """How specifically a pattern claims a path; None when it does not match.

    An exact path beats a directory prefix, which beats a glob. Longer wins within
    a kind, so 'src/app/tests/' takes a file away from 'src/app/'.
    """
    if any(ch in pattern for ch in "*?["):
        if fnmatch.fnmatchcase(path, pattern):
            return (1, len(pattern.replace("*", "").replace("?", "")))
        return None
    if pattern.endswith("/"):
        return (2, len(pattern)) if path.startswith(pattern) else None
    if path == pattern:
        return (3, len(pattern))
    if path.startswith(pattern + "/"):
        return (2, len(pattern) + 1)
    return None


def matches_any(patterns, path):
    return any(specificity(p, path) is not None for p in patterns)


SECRET_PATTERNS = [".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.jks", "*.keystore",
                   "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials*", ".credentials*",
                   "*.credentials.json", ".npmrc", ".pypirc", ".netrc", "secrets.*"]
GENERATED_DIRS = {"node_modules", "bower_components", "__pycache__", ".venv", "venv", "dist",
                  "build", "target", "bin", "obj", ".gradle", ".next", ".nuxt", ".tox",
                  ".pytest_cache", ".mypy_cache"}


def flag_reason(path):
    """Name the reason a path looks like a secret, a dependency or build output."""
    parts = path.split("/")
    name = parts[-1]
    for pattern in SECRET_PATTERNS:
        if fnmatch.fnmatchcase(name, pattern) and not name.endswith((".example", ".sample", ".template")):
            return "secret-like file name (%s)" % pattern
    for part in parts[:-1]:
        if part in GENERATED_DIRS:
            return "dependency or build-output directory (%s/)" % part
    return None
