"""Step-based progress lines on stderr.

stdout carries one JSON document per command and nothing else, so everything here
writes to stderr. Reporting changes no behaviour: it never touches the manifest,
the exit code or the result. It is off until the command line turns it on, so
code that imports the modules directly stays silent.
"""
import os
import sys
import time

GOOD = ("ready", "passed", "verified", "done", "created", "removed", "same")
BAD = ("failed", "mismatch", "changed", "refused")
WARN = ("blocked", "user_provided", "manual")
# Everything else (unverified, not_run, timed_out, skipped, exists, dry run) is neutral.

_COLOURS = {"good": "32", "bad": "31", "warn": "33", "neutral": "90", "info": "34", "bold": "1"}
_GLYPHS = {"good": u"✓", "bad": u"✗", "warn": "!", "neutral": u"–", "info": u"•",
           "running": u"…", "arrow": u"→", "dot": u"·"}
_ASCII = {"good": "OK", "bad": "FAIL", "warn": "!!", "neutral": "--", "info": "*",
          "running": "..", "arrow": "->", "dot": "-"}


def kind_of(status):
    if status in GOOD:
        return "good"
    if status in BAD:
        return "bad"
    if status in WARN:
        return "warn"
    return "neutral"


def _encodable(stream, text):
    try:
        text.encode(getattr(stream, "encoding", None) or "ascii")
        return True
    except (UnicodeError, LookupError):
        return False


class Reporter:
    def __init__(self, stream=None, enabled=False, colour=False, live=False):
        self.stream = stream
        self.enabled = enabled
        self.colour = colour
        self.live = live  # a terminal: a running line may be shown and then replaced
        self.command = ""
        self.total = 0
        self.number = 0
        self._pending = False
        self.glyphs = _GLYPHS if stream is not None and _encodable(stream, "".join(_GLYPHS.values())) else _ASCII

    def _paint(self, text, name):
        return "\033[%sm%s\033[0m" % (_COLOURS[name], text) if self.colour else text

    def _write(self, text, newline=True):
        if not self.enabled:
            return
        try:
            if self._pending:
                self.stream.write("\r\033[K")
                self._pending = False
            self.stream.write(text + ("\n" if newline else ""))
            self.stream.flush()
        except (OSError, ValueError, UnicodeError):
            self.enabled = False  # a broken stderr must never break a transfer

    def start(self, command, detail=""):
        self.command, self.total, self.number = command, 0, 0
        self._write(self._paint("[%s]" % command, "bold") + (" " + detail if detail else ""))

    def plan(self, total):
        self.total, self.number = total, 0

    def step(self, title):
        self.number += 1
        counter = "%d/%d" % (self.number, self.total) if self.total else "%d" % self.number
        self._write(" %s %s" % (self._paint(counter, "info"), title))

    def running(self, label):
        """Show what is in progress. Only on a terminal, where the line can be replaced."""
        if self.enabled and self.live:
            self._write("     %s %s" % (self._paint(self.glyphs["running"], "info"), label), newline=False)
            self._pending = True

    def item(self, label, status, detail=""):
        kind = kind_of(status)
        line = "     %s %s  %s" % (self._paint(self.glyphs[kind], kind), label, self._paint(status, kind))
        self._write(line + ("  " + detail if detail else ""))

    def finish(self, code, summary=""):
        kind = "good" if code == 0 else "bad"
        line = "%s %s exit %d" % (self._paint("[%s]" % self.command, "bold"),
                                  self._paint(self.glyphs[kind], kind), code)
        self._write(line + (" %s %s" % (self.glyphs["dot"], summary) if summary else ""))

    def arrow(self):
        return self.glyphs["arrow"]


_reporter = Reporter()


def configure(mode="auto", stream=None):
    """mode: 'auto' (colour and running lines on a terminal, plain text otherwise), 'plain' or 'off'."""
    global _reporter
    stream = stream or sys.stderr
    terminal = False
    if mode == "auto":
        try:
            terminal = stream.isatty() and os.environ.get("TERM") != "dumb"
        except (AttributeError, ValueError):
            terminal = False
    colour = terminal and "NO_COLOR" not in os.environ
    if colour and os.name == "nt":
        os.system("")  # turns on escape-sequence handling in the Windows console
    _reporter = Reporter(stream, enabled=mode != "off", colour=colour, live=terminal)
    return _reporter


def start(command, detail=""):
    _reporter.start(command, detail)


def plan(total):
    _reporter.plan(total)


def step(title):
    _reporter.step(title)


def running(label):
    _reporter.running(label)


def item(label, status, detail=""):
    _reporter.item(label, status, detail)


def finish(code, summary=""):
    _reporter.finish(code, summary)


def arrow():
    return _reporter.arrow()


def clock():
    return time.monotonic()


def seconds(since):
    return "%.1fs" % (time.monotonic() - since)
