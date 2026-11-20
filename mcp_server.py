"""MCP server (stdio, stdlib only) exposing the arm daemon as tools. Images come back inline, so the model sees them directly.

    claude mcp add arm -- /path/to/.venv/bin/python /path/to/mcp_server.py      (or operator/.mcp.json)
Needs `arm.py serve` running; ARM_PORT / ARM_SOCK as for the CLI.
"""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import arm  # noqa: E402

NUM = {"type": "number"}
TOOLS = {  # name: (description, {param: schema}, required)
    "look": ("Camera images; fixed cameras have a 5 cm grid labelled 'x,y' in cm. cams: top, front, wrist or all.",
             {"cams": {"type": "string", "default": "top"}}, []),
    "locate": ("Parks the arm, then lists objects seen by the top camera: id, x, y (cm), shape, kind, inside. Text only; image=true adds the "
               "marks image (expensive: only when the text is not enough).", {"image": {"type": "boolean"}}, []),
    "pick": ("Pick an object: by id from the last locate, or at a rough x, y (cm) which snaps to the nearest object.",
             {"id": {"type": "string"}, "x": NUM, "y": NUM}, []),
    "place": ("Place what is held: at a container id, or at x, y (cm).", {"id": {"type": "string"}, "x": NUM, "y": NUM}, []),
    "park": ("Move the arm out of the top camera's view. Do this before look or locate.", {}, []),
    "status": ("Joints, gripper, tip position (cm).", {}, []),
    "move": ("Manual recovery move: gripper tip to x, y, z cm, pointing down.", {"x": NUM, "y": NUM, "z": NUM, "yaw": NUM}, ["x", "y", "z"]),
    "grip": ("Manual recovery: open or close the gripper.", {"state": {"type": "string", "enum": ["open", "close"]}}, ["state"]),
    "relax": ("Emergency stop: motor torque off.", {}, []),
    "push": ("Slide a thin object (too low to grasp) toward tx, ty cm with closed jaws; rough, 2-4 cm.",
             {"id": {"type": "string"}, "x": NUM, "y": NUM, "tx": NUM, "ty": NUM}, ["tx", "ty"]),
    "background": ("Photograph the empty table once (no objects on it) to steady locate.", {}, []),
    "gpick": ("Taught-grid mode only: pick like `pick`.", {"id": {"type": "string"}, "x": NUM, "y": NUM}, []),
    "gplace": ("Taught-grid mode only: place like `place`.", {"id": {"type": "string"}, "x": NUM, "y": NUM}, []),
    "listen": ("Wait for the next spoken or typed instruction from the audience (live view). Returns instruction or null.",
               {"wait_s": NUM}, []),
    "gesture": ("Expressive move: wave, nod, yes or no.", {"name": {"type": "string", "enum": ["wave", "nod", "yes", "no"]}}, ["name"]),
    "say": ("Speak one short line aloud (also shown in the live view). Only one is spoken per instruction.", {"text": {"type": "string"}}, ["text"]),
}


def args_for(name, a):
    if name == "look":
        return a.get("cams", "top").split()
    if name in ("pick", "place", "gpick", "gplace"):
        return [a["id"]] if "id" in a else [a["x"], a["y"]]
    if name == "move":
        return [a["x"], a["y"], a["z"]] + (["--yaw", a["yaw"]] if "yaw" in a else [])
    if name == "grip":
        return [a["state"]]
    if name == "push":
        return ([a["id"]] if "id" in a else [a["x"], a["y"]]) + [a["tx"], a["ty"]]
    if name == "gesture":
        return [a["name"]]
    if name == "say":
        return [a["text"]]
    return []


SLIM = ("id", "x", "y", "shape", "kind", "inside", "yaw_deg")


def run_tool(name, a):
    if name == "listen":  # local inbox, not the daemon
        out = arm.listen(["--wait", str(a.get("wait_s", 300))])
        return {"content": [{"type": "text", "text": json.dumps(out)}], "isError": False}
    if name in ("locate", "look"):
        arm.call("park")  # the arm hides objects; saves the model a separate park turn
    ok, txt = arm.call(name, args_for(name, a))
    out = json.loads(txt)
    if name == "locate" and ok:  # every token here is re-read on every later turn: keep it small
        out["objects"] = [{k: o[k] for k in SLIM if k in o} for o in out["objects"]]
        img = out.pop("image")
        txt = json.dumps(out)
        if a.get("image"):
            out["image"] = img
    paths = [out["image"]] if "image" in out else list(out.get("images", {}).values())
    content = [{"type": "text", "text": txt}]
    for p in paths:
        content.append({"type": "image", "mimeType": "image/png", "data": base64.b64encode(Path(p).read_bytes()).decode()})
    return {"content": content, "isError": not ok}


def handle(msg):
    m, p = msg.get("method"), msg.get("params", {})
    if m == "initialize":
        return {"protocolVersion": p.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "arm", "version": "0.1"},
                "instructions": "Robot arm seen through cameras. Units: cm, robot base frame (x forward, y left, z up). "
                                "One physical tool call at a time; park before locate; confirm results with locate or look."}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d,
                           "inputSchema": {"type": "object", "properties": props, "required": req}}
                          for n, (d, props, req) in TOOLS.items()]}
    if m == "tools/call":
        if p["name"] not in TOOLS:
            raise KeyError(f"unknown tool {p['name']}")
        try:
            return run_tool(p["name"], p.get("arguments", {}))
        except Exception as e:  # daemon down, bad args: tell the model instead of failing the RPC
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if m == "ping":
        return {}
    raise KeyError(f"method not found: {m}")


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" not in msg:  # notification (e.g. notifications/initialized)
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except KeyError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
