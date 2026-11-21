"""MCP server (stdio, stdlib only) for the sorting tools in sorter.py. Label crops come back inline as images.

    claude mcp add sorter -- /path/to/.venv/bin/python /path/to/agent/mcp_server.py
AGENT_BACKEND=fake (default) uses agent/fixtures; real uses agent/real.py.
"""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sorter import Sorter  # noqa: E402

S = {"type": "string"}
TOOLS = {
    "plan_pick": ("Dry-run free-placement pickup with taught drop bin; never moves.", {"id": S, "bin": S}, ["id", "bin"]),
    "vision_preview": ("Read-only generic object detections and reach rejection reasons. No movement.", {}, []),  # name: (description, {param: schema}, required)
    "scan": ("Lists every pack on the table: id, product, code, expiry, days_left, recalled, in_reach, fragile. Call once per batch.", {}, []),
    "list_bins": ("Bins, and which product goes to which bin.", {}, []),
    "inspect": ("Close-up photo of one pack from the wrist camera. Read the printed name and pass it to sort as read_name.",
                {"id": S}, ["id"]),
    "sort": ("Pick one pack and place it in a bin, then verify. Refused (with the reason and the expected bin) if it breaks a rule. "
             "One call per pack, one at a time.",
             {"id": S, "bin": S, "reason": S, "read_name": S, "read_code": S, "read_expiry": S}, ["id", "bin", "reason"]),
    "flag": ("Leave a pack for a person, with the reason (out of reach, grasp failed twice, unclear label).",
             {"id": S, "reason": S}, ["id", "reason"]),
    "say": ("Speak one short line aloud as Friday (also shown on the dashboard). One line per instruction.", {"text": S}, ["text"]),
    "listen": ("Wait for the next spoken or typed instruction from the dashboard. Returns instruction, or null after wait_s.",
               {"wait_s": {"type": "number"}}, []),
    "summary": ("End-of-batch result: counts per bin, flagged, refused, left on the table, proof log path.", {}, []),
}
sorter = None


def run_tool(name, a):
    global sorter
    sorter = sorter or Sorter()
    if name == "plan_pick":
        if not hasattr(sorter.b, "plan_pick"):
            out = {"error": "Select AGENT_BACKEND=free_preview"}
        elif sorter.guard():
            out = {"error": sorter.guard()}
        else:
            out = sorter.b.plan_pick(a["id"], a["bin"])
    elif name == "vision_preview":
        if not hasattr(sorter.b, "diagnostics"):
            out = {"error": "Select AGENT_BACKEND=free_preview for this tool"}
        else:
            out = sorter.b.diagnostics()
    elif name == "sort":
        out = sorter.sort(a["id"], a["bin"], a["reason"], a.get("read_code"), a.get("read_expiry"), a.get("read_name"))
    elif name in ("inspect",):
        out = sorter.inspect(a["id"])
    elif name == "say":
        out = sorter.say(a["text"])
    elif name == "listen":
        out = sorter.listen(a.get("wait_s", 300))
    elif name == "flag":
        out = sorter.flag(a["id"], a["reason"])
    else:
        out = getattr(sorter, name)()
    content = []
    if name == "inspect" and not out.get("error"):
        content.append({"type": "image", "mimeType": "image/png", "data": base64.b64encode(Path(out.pop("image")).read_bytes()).decode()})
    content.insert(0, {"type": "text", "text": json.dumps(out)})
    return {"content": content, "isError": bool(out.get("error"))}


def handle(msg):
    m, p = msg.get("method"), msg.get("params", {})
    if m == "initialize":
        return {"protocolVersion": p.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "sorter", "version": "0.1"},
                "instructions": "Pharmacy pack sorter. scan once, decide a bin for every pack, then sort one pack at a time. "
                                "The tools enforce the rules; a refused sort says why and which bin is expected."}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props, "required": req}}
                          for n, (d, props, req) in TOOLS.items()]}
    if m == "tools/call":
        if p["name"] not in TOOLS:
            raise KeyError(f"unknown tool {p['name']}")
        try:
            return run_tool(p["name"], p.get("arguments", {}))
        except Exception as e:  # tell the model instead of failing the RPC
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
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
