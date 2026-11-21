"""MCP server (stdio, stdlib only) for the candy sorter in candy.py. The numbered camera image comes back inline.

Real motion only with CANDY_GO=1 in the environment; otherwise every pick is a dry run (solve only, nothing moves).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import candy  # noqa: E402

S = {"type": "string"}
GO = os.environ.get("CANDY_GO") == "1"
TOOLS = {
    "look": ("Camera photo of the whole table with every object numbered (c1, c2, ...): colour and whether it can be picked by "
             "position (pickable). Look at the image to see what things really are (brand, type, shape) and where they lie.", {}, []),
    "spots": ("The taped crosses on the sheet where candies are placed: id (cross1, cross2, ...) and whether the arm can reach it.", {}, []),
    "pick": ("Pick the candies on the given crosses into one bin, one after another, fast: ids like [\"cross3\", \"cross4\"], "
             "or [\"all\"] for every reachable cross (an empty cross is skipped: the jaws close on nothing). People can talk during it: "
             "'stop' pauses, 'bin 3' switches the bin for the rest, 'continue' resumes, 'cancel' ends after the candy in hand, "
             "'abort' stops now and puts a held candy back on its cross. "
             "Returns done, remaining, bin_now, heard_while_busy.",
             {"ids": {"type": "array", "items": S}, "bin": S}, ["ids", "bin"]),
    "session": ("What happened in this session so far: candies per bin, empty or missed crosses, aborts, the last things said. "
                "Use it for questions like 'what's in bin 1?' or after a restart.", {}, []),
    "end": ("End the session after a goodbye: Friday stops listening and is not restarted.", {}, []),
    "say": ("Speak one short line aloud as Friday.", {"text": S}, ["text"]),
    "listen": ("Wait for the next spoken or typed instruction. Returns instruction, or null after wait_s.", {"wait_s": {"type": "number"}}, []),
    "home": ("Move the arm to its home pose.", {}, []),
    "grip": ("Open (release whatever is held, right where the arm is) or close the gripper. For 'release', 'open your claws', 'let go'.",
             {"state": {"type": "string", "enum": ["open", "close"]}}, ["state"]),
}
ears = None


def run_tool(name, a):
    global ears
    content = []
    if name == "look":
        objs = candy.look()
        out = {"objects": [{k: o[k] for k in ("id", "colour", "pickable", "on_cross") if k in o} | ({"x_cm": o["x_cm"], "y_cm": o["y_cm"]} if o.get("pickable") else {})
                           for o in objs]}
        out["crosses_with_something"] = sorted({o["on_cross"] for o in objs if o.get("on_cross")})
        if objs and not any(o.get("pickable") for o in objs):
            out["note"] = "camera mapping not usable on this laptop: describe what you see, but pick only from the crosses (spots)"
        import base64
        content.append({"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode((candy.RUNS / "candies.jpg").read_bytes()).decode()})
    elif name == "spots":
        out = {"crosses": [{k: c[k] for k in ("id", "reachable")} for c in candy.spots()],
               "bins": [b["name"] for b in candy.real.cfg("motion.json")["poses"]["bins"]]}
    elif name == "pick":
        out = candy.pick(a["ids"], a["bin"], go=GO)
        if not GO:
            out["note"] = "dry run: CANDY_GO=1 is not set, nothing moved"
    elif name == "grip":
        candy.real.DRY = not GO
        g = candy.real.cfg("motion.json")["grasp"]
        try:
            opening = candy.real.grip(g["open"] if a["state"] == "open" else g["close"])
            out = {"gripper": a["state"], "opening": opening, "dry": not GO}
        except candy.real.Stopped as e:
            out = {"error": str(e) + ": say 'continue' first"}
        candy.log(event="grip", state=a["state"])
    elif name == "session":
        out = candy.session()
    elif name == "end":
        (candy.RUNS / "candy_end").touch()
        candy.real.DRY = not GO
        candy.real.home()
        out = {"ended": True}
    elif name == "say":
        ears = ears or candy.Ears([])
        ears.say(a["text"])
        out = {"said": a["text"]}
    elif name == "listen":
        out = candy.listen(a.get("wait_s", 300))
    else:
        candy.real.DRY = not GO
        candy.real.home()
        out = {"home": True, "dry": not GO}
    content.insert(0, {"type": "text", "text": json.dumps(out)})
    return {"content": content, "isError": bool(isinstance(out, dict) and out.get("error"))}


def handle(msg):
    m, p = msg.get("method"), msg.get("params", {})
    if m == "initialize":
        return {"protocolVersion": p.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "candy", "version": "0.1"},
                "instructions": "Candy sorter arm. look, then pick ids into a bin. The tools measure and move; you choose."}
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
