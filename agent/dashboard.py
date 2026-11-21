"""Live sorter dashboard: what Claude sees and decides, from runs/agent/{scan.json,proof.jsonl}. Localhost only.

    .venv/bin/python agent/dashboard.py          # http://127.0.0.1:8770
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUNS = Path(os.environ.get("AGENT_RUNS_DIR", Path(__file__).resolve().parent.parent / "runs" / "agent"))
PORT = int(os.environ.get("DASH_PORT", "8770"))
SAID = RUNS.parent / "said.jsonl"  # written by arm.Voice
STOP_WORDS = re.compile(r"\b(stop|wait|cancel|freeze|halt)\b", re.I)


def heard(text):
    """A person said or typed something: queue it for Claude; a stop word freezes the tools at once, without waiting for Claude."""
    text = text.strip()[:300]
    if not text:
        return
    (RUNS / "hush").touch()
    if STOP_WORDS.search(text):
        (RUNS / "STOP").touch()
    with (RUNS / "inbox.jsonl").open("a") as fh:
        fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text}) + "\n")


CONFIG = Path(os.environ.get("AGENT_CONFIG_DIR", Path(__file__).resolve().parent / "config"))


def layout():
    """Taught slot and bin positions (cm) and the step sequences, for the table map and the step tracker."""
    try:
        m = json.loads((CONFIG / "motion.json").read_text())
        w = json.loads((CONFIG / "workspace.json").read_text())
    except (OSError, ValueError):
        return {}
    cm = lambda p: {"x": round(p["x"] * 100, 1), "y": round(p["y"] * 100, 1)} if p else None  # noqa: E731
    return {"slots": [{"name": s["name"], **(cm(s.get("grip")) or {})} for s in m["poses"]["slots"]],
            "bins": [{"name": b["name"], **(cm(b.get("hover")) or {})} for b in m["poses"]["bins"]],
            "home": cm(m["poses"].get("home")), "reach": w.get("reach", {}),
            "sequences": {k: v for k, v in m.get("sequences", {}).items() if not k.startswith("_")}}


def state():
    rows = []
    if (RUNS / "proof.jsonl").exists():
        for line in (RUNS / "proof.jsonl").read_text().splitlines()[-2000:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    start = max((i for i, r in enumerate(rows) if r.get("session") == "start"), default=0)
    rows = rows[start:]  # current session only
    scan = json.loads((RUNS / "scan.json").read_text()) if (RUNS / "scan.json").exists() else {"packs": [], "bins": []}
    said = []  # Friday's lines from the last 20 s: the page drops mic transcripts that echo them
    if SAID.exists():
        for line in SAID.read_text().splitlines()[-20:]:
            r = json.loads(line)
            if time.time() - r["t"] < 20:
                said.append(r["text"])
    motion = []
    if (RUNS / "motion.jsonl").exists() and rows:
        since = rows[0].get("time", "")
        for line in (RUNS / "motion.jsonl").read_text().splitlines()[-500:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("time", "") >= since:
                motion.append(r)
    return {"layout": layout(), "session": rows[0] if rows else None, "events": rows[1:], "scan": scan, "motion": motion, "stopped": (RUNS / "STOP").exists(),
            "said": " ".join(said), "speaking": (SAID.parent / "speaking").exists()}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/state":
            self.send(200, json.dumps(state()).encode(), "application/json")
        elif self.path.startswith("/img/"):
            f = (RUNS / Path(self.path[5:]).name)  # only files directly in runs/agent
            ok = f.suffix in (".png", ".jpg") and f.is_file()
            self.send(200 if ok else 404, f.read_bytes() if ok else b"", "image/png" if f.suffix == ".png" else "image/jpeg")
        else:
            self.send(404, b"", "text/plain")

    def do_POST(self):
        if self.path == "/heard":
            n = min(int(self.headers.get("Content-Length", 0)), 4000)
            heard(json.loads(self.rfile.read(n) or b"{}").get("text", ""))
        elif self.path == "/hush":
            (RUNS / "hush").touch()
        elif self.path == "/stop":
            (RUNS / "STOP").touch()
        elif self.path == "/resume":
            (RUNS / "STOP").unlink(missing_ok=True)
        else:
            return self.send(404, b"", "text/plain")
        self.send(200, b"{}", "application/json")


PAGE = Path(__file__).with_name("dashboard.html")


if __name__ == "__main__":
    RUNS.mkdir(parents=True, exist_ok=True)
    print(f"sorter dashboard on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
