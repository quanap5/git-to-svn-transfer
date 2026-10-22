"""An SVN repository's history as a graph: lanes for trunk, branches and tags, forks and merges.

Built from 'svn log -v' and svn:mergeinfo only. Nothing in the repository is changed.

A lane is a directory of the standard layout, at any depth: '.../trunk',
'.../branches/<name>' or '.../tags/<name>'. A repository without that layout is
one lane, '/'. A fork is a lane created by copying another; a merge is a change
to svn:mergeinfo that adds revisions from another lane.
"""
import os
from urllib.parse import quote

from . import progress
from .common import G2SError, inside, write_text
from .dashboard import embed
from .render import ASSETS
from .svnops import Svn

TEMPLATE = "graph.template.html"
MERGEINFO_LOOKUPS = 100
KIND_ORDER = {"root": 0, "trunk": 0, "branch": 1, "tag": 2}


def lane_of(path):
    segments = [s for s in path.split("/") if s]
    for i, segment in enumerate(segments):
        if segment == "trunk":
            return "/" + "/".join(segments[:i + 1])
        if segment in ("branches", "tags") and i + 1 < len(segments):
            return "/" + "/".join(segments[:i + 2])
    return "/"


def kind_of(lane):
    if lane == "/":
        return "root"
    parts = lane.strip("/").split("/")
    if parts[-1] == "trunk":
        return "trunk"
    return "tag" if parts[-2] == "tags" else "branch"


def parse_mergeinfo(text):
    """'/branches/a:3-5,7*' lines -> {source path: [(first, last), ...]}."""
    result = {}
    for line in (text or "").splitlines():
        source, _, ranges = line.strip().rpartition(":")
        if not source:
            continue
        for part in ranges.split(","):
            first, _, last = part.strip().rstrip("*").partition("-")
            try:
                span = (int(first), int(last) if last else int(first))
            except ValueError:
                continue
            result.setdefault(source, []).append(span)
    return result


def newest_added(new, old):
    """The highest revision in the 'new' ranges that the 'old' ranges do not cover, or None."""
    best = None
    for first, last in new:
        top = last
        while top >= first:
            cover = next(((a, b) for a, b in old if a <= top <= b), None)
            if cover is None:
                best = top if best is None else max(best, top)
                break
            top = cover[0] - 1
    return best


def _place(entry):
    """The lane a revision belongs to: the one holding most of its paths, ignoring '/' when it can."""
    counts = {}
    for path in entry["paths"]:
        lane = lane_of(path["path"])
        counts[lane] = counts.get(lane, 0) + 1
    named = {lane: n for lane, n in counts.items() if lane != "/"}
    pick = named or counts or {"/": 0}
    lane = max(sorted(pick), key=lambda name: pick[name])
    return lane, sorted(name for name in counts if name not in (lane, "/"))


def _columns(lanes, newest):
    """Lowest free column per lane, trunk first. Trunk holds its column up to the newest shown
    revision; a branch or tag holds it only between its first and last revision, as git log --graph
    does, so a branch nobody deleted but nobody uses does not widen the graph. Lanes whose spans do
    not overlap share a column."""
    taken = []
    for lane in sorted(lanes, key=lambda l: (KIND_ORDER[l["kind"]], l["first"], l["id"])):
        end = newest if lane["kind"] in ("trunk", "root") else lane["last"]
        column = 0
        while any(c == column and not (e < lane["first"] or f > end) for c, f, e in taken):
            column += 1
        lane["column"] = column
        taken.append((column, lane["first"], end))


def build(repository, entries, mergeinfo=None, limit=None):
    """The graph of the given log entries. 'mergeinfo(lane path, revision)' returns the property text."""
    entries = sorted(entries, key=lambda e: -e["revision"])
    revisions, lane_revs, notes = [], {}, []
    for entry in entries:
        lane, others = _place(entry)
        revisions.append({"rev": entry["revision"], "author": entry["author"], "date": entry["date"],
                          "message": entry["message"], "lane": lane, "other_lanes": others,
                          "paths": entry["paths"]})
        lane_revs.setdefault(lane, []).append(entry["revision"])

    def newest(lane, at_most):
        return next((rev for rev in lane_revs.get(lane, []) if rev <= at_most), None)

    edges, closed = [], {}
    for revision in revisions:
        for path in revision["paths"]:
            lane = lane_of(path["path"])
            if lane == "/" or path["path"].rstrip("/") != lane:
                continue
            if path["action"] in ("A", "R") and path["copyfrom_path"]:
                source = lane_of(path["copyfrom_path"])
                found = newest(source, path["copyfrom_rev"])
                edges.append({"kind": "fork", "from_lane": source, "to_lane": lane, "to_rev": revision["rev"],
                              "from_rev": found, "source_rev": path["copyfrom_rev"],
                              "source_path": path["copyfrom_path"], "outside": found is None})
            elif path["action"] == "D":
                closed.setdefault(lane, revision["rev"])

    lookups, merges = 0, {}
    if mergeinfo:
        for revision in revisions:
            for path in revision["paths"]:
                lane = lane_of(path["path"])
                if not (path["prop_mods"] and path["action"] == "M" and lane != "/"
                        and (path["kind"] == "dir" or path["path"].rstrip("/") == lane)):
                    continue
                if lookups >= MERGEINFO_LOOKUPS:
                    if not any(n.startswith("merge lookups") for n in notes):
                        notes.append("merge lookups stopped after %d property changes; older merges are not drawn"
                                     % MERGEINFO_LOOKUPS)
                    continue
                lookups += 1
                new = parse_mergeinfo(mergeinfo(path["path"], revision["rev"]))
                old = parse_mergeinfo(mergeinfo(path["path"], revision["rev"] - 1))
                for source, spans in sorted(new.items()):
                    top = newest_added(spans, old.get(source, []))
                    source_lane = lane_of(source)
                    if top is None or source_lane == revision["lane"]:
                        continue
                    key = (source_lane, revision["rev"])
                    if key not in merges or merges[key]["source_rev"] < top:
                        found = newest(source_lane, top)
                        merges[key] = {"kind": "merge", "from_lane": source_lane, "to_lane": revision["lane"],
                                       "to_rev": revision["rev"], "from_rev": found, "source_rev": top,
                                       "source_path": source, "outside": found is None}
    edges += [merges[key] for key in sorted(merges, key=lambda k: (-k[1], k[0]))]

    # A lane is closed only when its deletion is its last event; a deleted and recreated branch lives on.
    lanes = [{"id": lane, "label": lane.lstrip("/") or "/", "kind": kind_of(lane), "first": min(revs),
              "last": max(revs), "closed": closed.get(lane) if closed.get(lane, 0) >= max(revs) else None,
              "revisions": len(revs)}
             for lane, revs in lane_revs.items()]
    _columns(lanes, entries[0]["revision"] if entries else 0)
    column = {lane["id"]: lane["column"] for lane in lanes}
    for revision in revisions:
        revision["column"] = column[revision["lane"]]

    truncated = bool(limit) and len(entries) >= limit and bool(entries) and entries[-1]["revision"] > 1
    if truncated:
        notes.append("only the newest %d revisions are shown; pass a larger --limit for older history" % limit)
    if any(e["outside"] for e in edges):
        notes.append("some forks or merges come from revisions older than the shown range")
    return {
        "repository": repository, "limit": limit, "truncated": truncated,
        "shown": {"newest": entries[0]["revision"], "oldest": entries[-1]["revision"]} if entries else None,
        "lanes": sorted(lanes, key=lambda l: (l["column"], l["first"])), "revisions": revisions, "edges": edges,
        "notes": notes,
        "counts": {"revisions": len(revisions), "lanes": len(lanes),
                   "forks": sum(1 for e in edges if e["kind"] == "fork"),
                   "merges": sum(1 for e in edges if e["kind"] == "merge")},
    }


def mark_checkout(data, checkout):
    """Record the working copy in the page data. It sits on the last revision that changed it, which is on
    its own lane, rather than on the repository-wide revision it was updated to."""
    changed = checkout["changed"]
    shown = {rev["rev"] for rev in data["revisions"]}
    data["checkout"] = {"revision": checkout["revision"], "changed": changed, "path": checkout["path"],
                        "lane": lane_of(checkout["path"]), "shown": changed in shown}
    if changed not in shown:
        data["notes"].append("the working copy (r%d, last changed r%s) is older than the shown range; "
                             "pass a larger --limit to see it" % (checkout["revision"], changed))


def write(path, data):
    with open(os.path.join(ASSETS, TEMPLATE), "r", encoding="utf-8") as handle:
        template = handle.read()
    write_text(path, template.replace("__G2S_DATA__", embed(data)))
    return path


def graph(svn_wc=None, url=None, out=None, limit=300, svn_bin=None):
    """Read a repository's history and write the graph page. Read-only towards SVN."""
    if limit < 1:
        raise G2SError("--limit must be at least 1")
    out = os.path.abspath(out)
    if svn_wc and inside(out, os.path.abspath(svn_wc)):
        raise G2SError("--out must be outside the SVN working copy, or svn would see the page as a new file")
    svn = Svn(svn_bin)
    progress.plan(4)
    progress.step("repository")
    target = svn.native(os.path.abspath(svn_wc)) if svn_wc else url
    repository = svn.repository(target)
    progress.item(repository["root"], "done", "head r%d" % repository["head"])
    progress.step("history, newest %d revisions" % limit)
    entries = svn.log(repository["root"], limit)
    progress.item("%d revision(s)" % len(entries), "done")
    progress.step("forks and merges")

    def mergeinfo(path, revision):
        return svn.mergeinfo(repository["root"] + quote(path, safe="/"), revision)

    data = build(repository, entries, mergeinfo, limit)
    if svn_wc:
        mark_checkout(data, svn.checkout(target))
    progress.item("%d lane(s), %d fork(s), %d merge(s)" % (
        data["counts"]["lanes"], data["counts"]["forks"], data["counts"]["merges"]), "done")
    progress.step("page")
    write(out, data)
    progress.item(out, "done")
    return dict({"html": out, "repository": repository["root"], "head": repository["head"],
                 "checkout": data.get("checkout"), "truncated": data["truncated"], "notes": data["notes"]},
                **data["counts"])
