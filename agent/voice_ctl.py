"""Voice control for batch_pick, without Claude: reads what the dashboard heard (runs/agent/inbox.jsonl, the words after
"Alfred") and turns fixed phrases into commands. Checked between every arm step, so a command acts within one step.

    stop / wait / pause        pause (the arm holds where it is); the dashboard also sets the STOP file at once
    continue / resume / go on  resume at the same step
    bin 3 / put them in bin 3  this candy and all later ones go to bin 3
    cancel / that's enough     finish the candy in hand, then home
    abort / drop it            put a held candy back where it was picked, then home
"""
import json
import re
import subprocess
import time
from pathlib import Path

import real

INBOX = real.RUNS / "inbox.jsonl"
SAID = real.RUNS.parent / "said.jsonl"  # the dashboard ignores mic text that echoes these lines
NUM = {"one": 1, "won": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4, "for": 4, "five": 5}
WORDS = {"abort": r"\b(abort|emergency|drop it|put it back)\b", "cancel": r"\b(cancel|that'?s enough|enough|finish)\b",
         "stop": r"\b(stop|wait|pause|freeze|halt|hold on)\b", "resume": r"\b(continue|resume|go on|carry on|keep going)\b"}


def say(text):
    """Speak without blocking the arm; logged so the dashboard mic does not hear itself."""
    print(f"VOICE: {text}", flush=True)
    SAID.parent.mkdir(parents=True, exist_ok=True)
    with SAID.open("a") as fh:
        fh.write(json.dumps({"t": time.time(), "text": text}) + "\n")
    try:
        subprocess.Popen(["say", "--", text])
    except OSError:
        pass


def bin_in(text):
    m = re.search(r"\bbin\s*(\d|one|won|two|to|too|three|four|for|five)\b", text)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else NUM[m.group(1)]
    return f"bin{n}" if 1 <= n <= 5 else None


def parse(text):
    """Text -> list of (command, arg). Several can come in one sentence ("stop, put the rest in bin 3")."""
    t = text.lower()
    out = [(k, None) for k, pat in WORDS.items() if re.search(pat, t)]
    b = bin_in(t)
    if b:
        out.append(("bin", b))
    return out


class Control:
    """Reads new inbox lines (from the moment it was made) and keeps the run state: bin, paused, cancel, abort."""

    def __init__(self, bin_name):
        self.bin = bin_name
        self.pos = INBOX.stat().st_size if INBOX.exists() else 0
        self.cancel = self.abort = False

    def lines(self):
        if not INBOX.exists():
            return []
        with INBOX.open() as fh:
            fh.seek(self.pos)
            new = fh.read()
            self.pos = fh.tell()
        return [json.loads(x)["text"] for x in new.splitlines() if x.strip()]

    def poll(self):
        """Apply new commands. Returns the list of commands seen (for logging)."""
        seen = []
        for text in self.lines():
            for cmd, arg in parse(text):
                seen.append(cmd)
                if cmd == "abort":
                    self.abort = True
                elif cmd == "cancel":
                    self.cancel = True
                    say("Okay, finishing this one.")
                elif cmd == "bin" and arg != self.bin:
                    self.bin = arg
                    say(f"Switching to bin {arg[3:]}.")
        return seen

    def wait_if_paused(self):
        """Between steps: hold while STOP is set (the dashboard sets it on a stop word, clears it on continue)."""
        self.poll()
        if not real.STOP.exists() or self.abort:
            return
        say("Paused. Say Alfred, continue.")
        while real.STOP.exists() and not self.abort:
            time.sleep(0.1)
            seen = self.poll()
            if "resume" in seen and "stop" not in seen and "abort" not in seen:  # a stop word always wins
                real.STOP.unlink(missing_ok=True)  # also when the dashboard is an older version
        if not self.abort:
            say("Continuing.")


class Aborted(Exception):
    pass


def check(ctl):
    """Call before every arm step."""
    if ctl is None:
        return
    ctl.wait_if_paused()
    if ctl.abort:
        raise Aborted()


if __name__ == "__main__":  # quick self-test of the phrase rules
    for s in ("stop", "please continue", "put the rest in bin three", "bin 2", "cancel that", "abort", "drop it now",
              "stop, put them in bin for"):
        print(f"{s!r:32} -> {parse(s)}")
    print(Path(__file__).name, "ok")
