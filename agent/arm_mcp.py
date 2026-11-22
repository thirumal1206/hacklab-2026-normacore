"""MCP server (stdio, stdlib only) over arm_server.py: one arm, the pack picks the job.

    .venv/bin/python agent/arm_server.py [--go]     # owns the arm and its safety
    claude mcp add feeder -e PACK=feeder -- /path/to/.venv/bin/python /path/to/agent/arm_mcp.py
PACK selects operator/packs/<PACK>/pack.json (tools + instructions). No motion code here: every tool is an HTTP call.
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = f"http://127.0.0.1:{os.environ.get('ARM_SERVER_PORT', '8772')}"
PACK_DIR = REPO / "operator" / "packs" / os.environ.get("PACK", "feeder")
PACK = json.loads((PACK_DIR / "pack.json").read_text())

S, N = {"type": "string"}, {"type": "number"}
ALL = {  # name: (description, properties, required, method, path)
    "objects": ("Objects on the table now: id, label, colour, position, pickable and why not.", {}, [], "GET", "/api/objects"),
    "pick": ("Start picking these object ids into a bin (bin1..bin5). Returns at once; call wait for the result.",
             {"ids": {"type": "array", "items": N}, "bin": S}, ["ids"], "POST", "/api/pick"),
    "set_bin": ("Send the rest of the running pick to another bin.", {"bin": S}, ["bin"], "POST", "/api/bin"),
    "feed": ("Take the candy from the taught spot and bring it slowly to the person's mouth, then drop it. "
             "Returns at once; call wait for the result.", {}, [], "POST", "/api/feed"),
    "home": ("Move the arm home (slowly if it is near the person). Use on 'go back'.", {}, [], "POST", "/api/home"),
    "stop": ("Pause the arm where it is.", {}, [], "POST", "/api/stop"),
    "resume": ("Continue after a stop.", {}, [], "POST", "/api/resume"),
    "cancel": ("Finish the object in hand, then home.", {}, [], "POST", "/api/cancel"),
    "abort": ("Stop now and put a held object back.", {}, [], "POST", "/api/abort"),
    "gripper": ("Open or close the jaws.", {"action": {"type": "string", "enum": ["open", "close"]}}, ["action"],
                "POST", "/api/gripper"),
    "wait": ("Wait until the arm is idle (or wait_s passes) and return what it reported meanwhile.",
             {"wait_s": N}, [], None, None),
}
TOOLS = {n: ALL[n] for n in PACK["tools"]}
seen = 0  # last event seq returned to the model


def http(method, path, body=None):
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def wait(wait_s=60):
    global seen
    end, evs, r = time.time() + min(float(wait_s), 300), [], {}
    while True:
        r = http("GET", f"/api/events?after={seen}")
        evs += r["events"]
        seen = max([seen] + [e["seq"] for e in r["events"]])
        if not r["busy"] or time.time() > end:
            break
        time.sleep(0.5)
    return {"busy": r["busy"], "stopped": r["stopped"], "dry": r["dry"], "events": [f"{e['kind']}: {e['text']}" for e in evs]}


def run_tool(name, a):
    global seen
    _, _, _, method, path = TOOLS[name]
    if name == "wait":
        return wait(a.get("wait_s", 60))
    if method == "POST" and name in ("pick", "feed"):  # report only what this action causes
        seen = max([seen] + [e["seq"] for e in http("GET", f"/api/events?after={seen}")["events"]])
    return http(method, path, a)


def handle(msg):
    m, p = msg.get("method"), msg.get("params", {})
    if m == "initialize":
        return {"protocolVersion": p.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": f"arm-{PACK_DIR.name}", "version": "0.1"}, "instructions": PACK["instructions"]}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": pr, "required": rq}}
                          for n, (d, pr, rq, _, _) in TOOLS.items()]}
    if m == "tools/call":
        if p["name"] not in TOOLS:
            raise KeyError(f"unknown tool {p['name']}")
        try:
            out = run_tool(p["name"], p.get("arguments", {}))
        except OSError as e:  # server down: tell the model, do not fail the RPC
            out = {"error": f"arm_server not reachable on {BASE}: {e}"}
        return {"content": [{"type": "text", "text": json.dumps(out)}], "isError": bool(out.get("error"))}
    if m == "ping":
        return {}
    raise KeyError(f"method not found: {m}")


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except KeyError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
